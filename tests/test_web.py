import hashlib
import re
from dataclasses import fields, replace
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import app.web as web_module
from app.config import Config, TelegramChannel
from app.service import SpotifyTrack, TikTokToTelegram, Video, YouTubeVideo
from app.web import create_app
from app.web_passwords import check_password_hash, generate_password_hash


class FakeStorage:
    def __init__(self) -> None:
        self.marked = None

    def mark(self, video_id: str, username: str) -> None:
        self.marked = (video_id, username)


class FakeService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.storage = FakeStorage()
        self.prepare_result = None
        self.published = None
        self.published_youtube = None
        self.published_spotify = None
        self.marked_processed = None
        self.imported = None

    def prepare_url(self, url: str):
        if self.prepare_result is not None:
            return self.prepare_result
        if "instagram.com" in url:
            platform = "instagram"
        elif "x.com" in url or "twitter.com" in url:
            platform = "twitter"
        elif "reddit.com" in url or "redd.it" in url:
            platform = "reddit"
        else:
            platform = "tiktok"
        return (
            Video(
                "123",
                "author",
                "Исходный текст",
                url,
                0,
                platform=platform,
            ),
            self.path,
        )

    def publish(
        self,
        video: Video,
        paths: tuple[Path, ...],
        quote_text: str,
        before_text: str,
        after_text: str,
        chat_id: str,
        include_author: bool = True,
        include_description: bool = True,
        caption_html: str | None = "",
    ) -> None:
        self.published = (
            video,
            paths,
            quote_text,
            before_text,
            after_text,
            chat_id,
            include_author,
            include_description,
            caption_html,
        )

    def mark_processed(self, video: Video) -> None:
        self.marked_processed = video

    def import_channel(self, url: str, post_existing: bool, chat_id: str):
        self.imported = (url, post_existing, chat_id)
        return 15, 4 if post_existing else 0

    def get_youtube_info(self, url: str) -> YouTubeVideo:
        return YouTubeVideo(
            "yt1",
            "YouTube title",
            url,
            "https://i.ytimg.com/test.jpg",
            60,
            "Channel",
        )

    def download_youtube(self, video: YouTubeVideo, output_id: str) -> Path:
        path = self.path.parent / f"{output_id}.mp4"
        path.write_bytes(b"youtube")
        return path

    def publish_youtube(
        self,
        video: YouTubeVideo,
        before_text: str,
        after_text: str,
        chat_id: str,
        caption_html: str | None = "",
    ) -> None:
        self.published_youtube = (
            video,
            before_text,
            after_text,
            chat_id,
            caption_html,
        )

    def get_spotify_info(self, url: str) -> SpotifyTrack:
        return SpotifyTrack(
            "4uLU6hMCjMI75M1A2tKUQC",
            "Spotify title",
            url,
            "https://i.scdn.co/image/test.jpg",
            "Spotify artist",
        )

    def download_spotify(self, track: SpotifyTrack, output_id: str) -> Path:
        path = self.path.parent / f"{output_id}.mp3"
        path.write_bytes(b"mp3")
        return path

    def publish_spotify(
        self,
        track: SpotifyTrack,
        path: Path,
        before_text: str,
        after_text: str,
        chat_id: str,
        caption_html: str | None = "",
    ) -> None:
        self.published_spotify = (
            track,
            path,
            before_text,
            after_text,
            chat_id,
            caption_html,
        )

    def add_telegram_destination(self, name: str, chat_id: str, bot_token: str) -> None:
        self.added_destination = (name, chat_id, bot_token)

    def discover_telegram_destinations(self, bot_token: str):
        self.discovered_token = bot_token
        return (TelegramChannel("Private", "-1001234567890"),)

    def delete_telegram_destination(self, chat_id: str) -> None:
        self.deleted_destination = chat_id

    def move_telegram_destination(self, chat_id: str, direction: str) -> None:
        self.moved_destination = (chat_id, direction)

    def add_monitored_tiktok_channel(self, channel: str) -> None:
        self.added_monitor = channel

    def delete_monitored_tiktok_channel(self, channel: str) -> None:
        self.deleted_monitor = channel

    def set_poll_interval_seconds(self, value: str) -> None:
        self.updated_interval = value

    def update_cookies(self, service_name: str, content: bytes) -> None:
        self.updated_cookies = (service_name, content)


def make_config(tmp_path: Path, **overrides) -> Config:
    values = {
        "telegram_bot_token": "token",
        "telegram_chat_id": "@channel",
        "tiktok_channels": (),
        "poll_interval_seconds": 300,
        "scan_limit": 15,
        "post_existing": False,
        "data_dir": tmp_path,
        "cookies_file": None,
        "instagram_cookies_file": None,
        "youtube_cookies_file": None,
        "youtube_po_token_provider_url": None,
        "web_host": "127.0.0.1",
        "web_port": 8080,
        "web_username": None,
        "web_password": None,
        "telegram_channels": (
            TelegramChannel("Main", "@channel"),
            TelegramChannel("Second", "@second"),
        ),
        "spotify_cookies_file": None,
        "twitter_cookies_file": None,
        "reddit_cookies_file": None,
        # These fields are optional during the Config migration. Keeping them
        # here makes the tests exercise the dedicated bootstrap credentials as
        # soon as the fields land without breaking an older Config constructor.
        "setup_username": None,
        "setup_password": None,
    }
    config_fields = {field.name for field in fields(Config)}
    values.update({key: value for key, value in overrides.items() if key in config_fields})
    return Config(**{key: value for key, value in values.items() if key in config_fields})


def setup_config(tmp_path: Path) -> tuple[Config, str, str]:
    username = "bootstrap"
    password = "bootstrap-secret"
    config_fields = {field.name for field in fields(Config)}
    if {"setup_username", "setup_password"} <= config_fields:
        return (
            make_config(
                tmp_path,
                web_username="legacy-web-user",
                web_password="legacy-web-password",
                setup_username=username,
                setup_password=password,
            ),
            username,
            password,
        )
    return (
        make_config(tmp_path, web_username=username, web_password=password),
        username,
        password,
    )


def finish_admin_setup(client, username: str, password: str):
    response = client.post(
        "/setup-admin",
        json={
            "setup_username": username,
            "setup_password": password,
            "password": "admin-password",
            "confirm_password": "admin-password",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "redirect": "/"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    return response


def make_client(config: Config, service) -> TestClient:
    return TestClient(create_app(config, service), follow_redirects=False)


def test_password_hashes_round_trip_and_legacy_pbkdf2_stays_compatible() -> None:
    current = generate_password_hash("correct horse battery staple")
    assert check_password_hash(current, "correct horse battery staple")
    assert not check_password_hash(current, "wrong")

    salt = "legacy-salt"
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        b"legacy-password",
        salt.encode(),
        600_000,
    ).hex()
    legacy = f"pbkdf2:sha256:600000${salt}${digest}"
    assert check_password_hash(legacy, "legacy-password")
    assert not check_password_hash(legacy, "wrong")


@pytest.fixture(autouse=True)
def built_vue_spa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spa_dir = tmp_path / "vue-dist"
    spa_dir.mkdir()
    (spa_dir / "index.html").write_text(
        '<!doctype html><html><body><div id="app">ClipRelay Vue SPA</div></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(web_module, "SPA_DIRECTORY", spa_dir)


def test_spa_index_bootstrap_and_settings_contracts(tmp_path: Path) -> None:
    config = replace(
        make_config(tmp_path),
        telegram_channels=(
            TelegramChannel("Одинаковый чат", "-100111", "group"),
            TelegramChannel("Одинаковый чат", "-100222", "supergroup"),
        ),
    )
    client = make_client(config, FakeService(tmp_path / "video.mp4"))

    for route in ("/", "/settings", "/done"):
        response = client.get(route)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert '<div id="app">ClipRelay Vue SPA</div>' in response.text
    favicon = client.get("/static/favicon.ico")
    assert favicon.status_code == 200
    assert favicon.content
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "no-store"
    assert bootstrap.headers["x-content-type-options"] == "nosniff"
    assert bootstrap.headers["x-frame-options"] == "DENY"
    payload = bootstrap.json()
    assert payload["auth_supported"] is False
    assert payload["authenticated"] is True
    assert payload["setup_required"] is False
    assert payload["user"] is None
    assert payload["permissions"]["twitter"] is True
    assert payload["permissions"]["reddit"] is True
    assert {"twitter", "reddit"} <= {
        service["id"] for service in payload["services"]
    }
    assert payload["telegram_channels"][0]["display"] == "ID 100111"
    assert payload["telegram_channels"][0]["kind_label"] == "чат"

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    payload = settings.json()
    assert payload["settings_user"] is None
    assert payload["admin_mode"] is False
    assert payload["poll_interval_seconds"] == 300
    assert payload["monitored_tiktok_channels"] == []
    assert [channel["display"] for channel in payload["telegram_channels"]] == [
        "ID 100111",
        "ID 100222",
    ]
    assert {"twitter", "reddit"} <= set(payload["permissions"])
    assert {"twitter", "reddit"} <= {
        service["id"] for service in payload["cookie_services"]
    }


def test_basic_auth_still_guards_spa_and_api(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        web_username="admin",
        web_password="secret",
    )
    client = make_client(config, FakeService(tmp_path / "video.mp4"))

    assert client.get("/").status_code == 401
    assert client.get("/api/settings").status_code == 401
    headers = {"Authorization": "Basic YWRtaW46c2VjcmV0"}
    assert client.get("/", headers=headers).status_code == 200
    assert client.get("/api/settings", headers=headers).status_code == 200


def test_settings_cookie_services_include_local_cookie_status(tmp_path: Path) -> None:
    class CookieStatusService(FakeService):
        def cookie_status(self, service_name: str) -> dict[str, object]:
            if service_name == "twitter":
                return {
                    "uploaded": True,
                    "valid": True,
                    "reason": "ready",
                    "cookie_count": 2,
                }
            return {
                "uploaded": False,
                "valid": False,
                "reason": "missing",
                "cookie_count": 0,
            }

    client = make_client(
        make_config(tmp_path),
        CookieStatusService(tmp_path / "video.mp4"),
    )

    services = {
        item["id"]: item for item in client.get("/api/settings").json()["cookie_services"]
    }
    assert services["twitter"]["cookies"] == {
        "uploaded": True,
        "valid": True,
        "reason": "ready",
        "cookie_count": 2,
    }
    assert services["reddit"]["cookies"] == {
        "uploaded": False,
        "valid": False,
        "reason": "missing",
        "cookie_count": 0,
    }


def test_settings_fetch_mutations_return_updated_api_contract(tmp_path: Path) -> None:
    service = FakeService(tmp_path / "video.mp4")
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/settings/telegram",
        json={"name": "News", "chat_id": "@news", "bot_token": "123:secret"},
    )
    assert response.status_code == 200
    assert response.json()["event"] == "telegram-added"
    assert response.json()["settings"]["permissions"]["twitter"] is True
    assert service.added_destination == ("News", "@news", "123:secret")

    response = client.post(
        "/settings/telegram/discover",
        json={"bot_token": "123:secret"},
    )
    assert response.status_code == 200
    assert response.json()["found"] == 1
    assert response.json()["event"] == "telegram-discovered"

    response = client.post(
        "/settings/telegram/move",
        json={"chat_id": "@second", "direction": "up"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert service.moved_destination == ("@second", "up")

    response = client.post(
        "/settings/telegram/delete",
        json={"chat_id": "@second"},
    )
    assert response.status_code == 200
    assert response.json()["event"] == "telegram-deleted"
    assert service.deleted_destination == "@second"

    assert client.post(
        "/settings/tiktok/monitor",
        json={"channel": "@author"},
    ).json()["event"] == "tiktok-monitor-added"
    assert service.added_monitor == "@author"

    assert client.post(
        "/settings/tiktok/monitor/delete",
        json={"channel": "@author"},
    ).json()["event"] == "tiktok-monitor-deleted"
    assert service.deleted_monitor == "@author"

    assert client.post(
        "/settings/tiktok/interval",
        json={"poll_interval_seconds": "120"},
    ).json()["event"] == "tiktok-interval-updated"
    assert service.updated_interval == "120"

    response = client.post(
        "/settings/cookies/twitter",
        files={
            "cookies_file": (
                "cookies.txt",
                b"# Netscape HTTP Cookie File\n",
                "text/plain",
            )
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert response.json()["event"] == "twitter-cookies-updated"
    assert service.updated_cookies == (
        "twitter",
        b"# Netscape HTTP Cookie File\n",
    )


def test_media_job_api_json_send_and_cancel(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    service = FakeService(path)
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/media/info",
        json={
            "media_url": "https://www.instagram.com/reel/abc/",
            "chat_id": "@second",
        },
    )
    assert response.status_code == 200
    info = response.json()
    assert info["post"]["platform"] == "instagram"
    assert info["post"]["description"] == "Исходный текст"
    assert info["selected_chat_id"] == "@second"
    assert info["preview_url"].startswith("/preview/")
    assert info["preview_urls"] == []
    assert info["download_url"] == info["video_download_url"]
    assert info["download_url"].startswith("/media/video/")
    assert info["send_url"].startswith("/send/")
    assert info["cancel_url"].startswith("/cancel/")

    job = client.get(f"/api/media/jobs/{info['job_id']}?chat_id=@channel")
    assert job.status_code == 200
    assert job.json()["job_id"] == info["job_id"]
    assert job.json()["selected_chat_id"] == "@channel"
    assert client.get(
        f"/api/media/jobs/{info['job_id']}?chat_id=@unknown"
    ).status_code == 400
    assert client.get(info["post_url"]).status_code == 200
    assert client.get(info["preview_url"]).content == b"video"

    response = client.post(
        info["send_url"],
        json={
            "before_text": "До цитаты",
            "quote_text": "Новая цитата",
            "after_text": "После цитаты",
            "chat_id": "@second",
            "caption_options_present": "1",
            "include_author": False,
            "include_description": True,
            "caption_html": "<b>Готовый</b> <tg-spoiler>пост</tg-spoiler>",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "redirect": "/done"}
    assert service.published[2:8] == (
        "Новая цитата",
        "До цитаты",
        "После цитаты",
        "@second",
        False,
        True,
    )
    assert service.published[8] == "<b>Готовый</b> <tg-spoiler>пост</tg-spoiler>"
    assert service.marked_processed.video_id == "123"
    assert client.get(f"/api/media/jobs/{info['job_id']}").status_code == 404
    assert not path.exists()

    path.write_bytes(b"video")
    info = client.post(
        "/media/info",
        json={"media_url": "https://www.tiktok.com/@author/video/123"},
    ).json()
    response = client.post(info["cancel_url"], json={})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "redirect": "/"}
    assert not path.exists()


def test_image_media_job_api_selection_and_zip_download(tmp_path: Path) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    service = FakeService(first)
    service.prepare_result = (
        Video(
            "post1",
            "creator",
            "Photos",
            "https://www.instagram.com/p/post1/",
            0,
            platform="instagram",
            media_type="image",
        ),
        (first, second),
    )
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/media/info",
        json={
            "media_url": "https://www.instagram.com/p/post1/",
            "chat_id": "@second",
        },
    )
    assert response.status_code == 200
    info = response.json()
    assert info["media_type"] == "image"
    assert info["image_count"] == 2
    assert len(info["preview_urls"]) == 2
    assert info["preview_urls"][0].endswith("/0")
    assert info["preview_urls"][1].endswith("/1")
    assert client.get(f"/api/media/jobs/{info['job_id']}").json()[
        "preview_urls"
    ] == info["preview_urls"]

    download = client.get(info["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(download.content)) as archive:
        assert len(archive.namelist()) == 2

    response = client.post(
        info["send_url"],
        json={
            "image_selection_present": "1",
            "selected_image_indices": [],
            "chat_id": "@second",
        },
    )
    assert response.status_code == 502
    assert response.json()["ok"] is False
    assert "Выберите хотя бы одно изображение" in response.json()["error"]
    assert first.exists()
    assert second.exists()

    response = client.post(
        info["send_url"],
        json={
            "image_selection_present": "1",
            "selected_image_indices": [1],
            "chat_id": "@second",
        },
    )
    assert response.status_code == 200
    assert service.published[1] == (second,)
    assert not first.exists()
    assert not second.exists()


def test_text_only_media_job_has_null_assets_and_sends_empty_tuple(
    tmp_path: Path,
) -> None:
    service = FakeService(tmp_path / "unused")
    service.prepare_result = (
        Video(
            "1234567890",
            "poster",
            "Только текст",
            "https://x.com/poster/status/1234567890",
            0,
            platform="twitter",
            media_type="text",
        ),
        (),
    )
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/media/info",
        json={
            "media_url": "https://x.com/poster/status/1234567890",
            "chat_id": "@channel",
        },
    )
    assert response.status_code == 200
    info = response.json()
    assert info["post"]["platform"] == "twitter"
    assert info["post"]["media_type"] == "text"
    assert info["preview_url"] is None
    assert info["preview_urls"] == []
    assert info["download_url"].startswith("/media/video/")
    assert info["video_download_url"] == info["download_url"]
    assert info["image_count"] == 0

    job = client.get(f"/api/media/jobs/{info['job_id']}")
    assert job.status_code == 200
    assert job.json()["preview_url"] is None
    assert job.json()["download_url"] == info["download_url"]

    download = client.get(info["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/plain")
    assert "attachment" in download.headers["content-disposition"]
    assert "Только текст" in download.text
    assert "https://x.com/poster/status/1234567890" in download.text

    response = client.post(
        info["send_url"],
        json={
            "before_text": "Вступление",
            "caption_html": "<b>Текстовый пост</b>",
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert service.published[1] == ()
    assert service.published[3] == "Вступление"
    assert service.marked_processed.platform == "twitter"


def test_youtube_job_api_download_and_json_send(tmp_path: Path) -> None:
    service = FakeService(tmp_path / "video.mp4")
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/youtube/info",
        json={"youtube_url": "https://www.youtube.com/watch?v=abc"},
    )
    assert response.status_code == 200
    info = response.json()
    assert info["video"]["title"] == "YouTube title"
    assert info["video"]["channel"] == "Channel"
    assert info["video"]["duration"] == 60
    assert info["thumbnail_download_url"].startswith("/youtube/thumbnail/")
    assert info["video_download_url"].startswith("/youtube/video/")
    assert info["send_url"].startswith("/youtube/send/")

    job = client.get(f"/api/youtube/jobs/{info['job_id']}")
    assert job.status_code == 200
    assert job.json()["video"] == info["video"]
    assert client.get(info["post_url"]).status_code == 200

    download = client.get(info["video_download_url"])
    assert download.status_code == 200
    assert download.content == b"youtube"
    assert "YouTube_title.mp4" in download.headers["Content-Disposition"]
    download.close()

    response = client.post(
        info["send_url"],
        json={
            "before_text": "До",
            "after_text": "После",
            "chat_id": "@second",
            "caption_html": '<a href="https://youtube.com/watch?v=abc">Ссылка</a>',
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "redirect": "/done?source=youtube",
    }
    assert service.published_youtube[1:4] == ("До", "После", "@second")
    assert service.published_youtube[4] == (
        '<a href="https://youtube.com/watch?v=abc">Ссылка</a>'
    )
    assert client.get(f"/api/youtube/jobs/{info['job_id']}").status_code == 404


def test_spotify_track_api_audio_and_json_send(tmp_path: Path) -> None:
    service = FakeService(tmp_path / "video.mp4")
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/spotify/info",
        json={
            "spotify_url": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
            "chat_id": "@second",
        },
    )
    assert response.status_code == 200
    info = response.json()
    assert info["track"]["title"] == "Spotify title"
    assert info["track"]["artist"] == "Spotify artist"
    assert info["audio_download_url"].endswith("4uLU6hMCjMI75M1A2tKUQC")
    assert client.get(info["post_url"]).status_code == 200

    track = client.get(
        "/api/spotify/tracks/4uLU6hMCjMI75M1A2tKUQC?chat_id=@second"
    )
    assert track.status_code == 200
    track_payload = track.json()
    assert track_payload["track"] == info["track"]
    assert track_payload["selected_chat_id"] == "@second"
    assert track_payload["send_url"].endswith("4uLU6hMCjMI75M1A2tKUQC")

    download = client.get(info["audio_download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/mpeg")
    assert download.content == b"mp3"
    assert "Spotify_title.mp3" in download.headers["Content-Disposition"]

    response = client.post(
        track_payload["send_url"],
        json={
            "before_text": "До",
            "after_text": "После",
            "chat_id": "@second",
            "caption_html": "<b>Готовый трек</b>",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "redirect": "/done?source=spotify",
    }
    assert service.published_spotify[2:] == (
        "До",
        "После",
        "@second",
        "<b>Готовый трек</b>",
    )


def test_form_routes_remain_backwards_compatible(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    service = FakeService(path)
    client = make_client(make_config(tmp_path), service)

    response = client.post(
        "/prepare",
        data={"tiktok_url": "https://www.tiktok.com/@author/video/123"},
    )
    assert response.status_code == 302
    job_id = re.search(r"/media/post/([0-9a-f]+)", response.headers["location"]).group(1)
    response = client.post(
        f"/send/{job_id}",
        data={
            "before_text": "До",
            "quote_text": "Цитата",
            "after_text": "После",
            "caption_html": "<b>Пост</b>",
        },
    )
    assert response.status_code == 302
    assert service.published[2:6] == ("Цитата", "До", "После", "@channel")

    path.write_bytes(b"video")
    response = client.post(
        "/prepare",
        data={"tiktok_url": "https://www.tiktok.com/@author/video/123"},
    )
    job_id = re.search(r"/media/post/([0-9a-f]+)", response.headers["location"]).group(1)
    assert client.post(f"/cancel/{job_id}").status_code == 302
    assert not path.exists()

    response = client.post(
        "/tiktok/prepare",
        data={
            "tiktok_url": "https://www.tiktok.com/@author",
            "post_existing": "on",
            "chat_id": "@second",
        },
    )
    assert response.status_code == 302
    assert service.imported == (
        "https://www.tiktok.com/@author",
        True,
        "@second",
    )

    response = client.post(
        "/settings/telegram",
        data={"name": "News", "chat_id": "@news", "bot_token": "123:secret"},
    )
    assert response.status_code == 302
    assert service.added_destination == ("News", "@news", "123:secret")

    response = client.post(
        "/settings/cookies/reddit",
        files={"cookies_file": ("cookies.txt", b"cookies", "text/plain")},
    )
    assert response.status_code == 302
    assert service.updated_cookies == ("reddit", b"cookies")

    youtube = client.post(
        "/youtube/info",
        data={"youtube_url": "https://www.youtube.com/watch?v=abc"},
    ).json()
    response = client.post(
        youtube["send_url"],
        data={"before_text": "До", "chat_id": "@second"},
    )
    assert response.status_code == 302

    spotify = client.post(
        "/spotify/info",
        data={
            "spotify_url": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
        },
    ).json()
    response = client.post(
        "/spotify/send/4uLU6hMCjMI75M1A2tKUQC",
        data={"chat_id": "@second"},
    )
    assert response.status_code == 302
    assert spotify["track_id"] == "4uLU6hMCjMI75M1A2tKUQC"


def test_setup_admin_requires_configured_bootstrap_credentials(
    tmp_path: Path,
) -> None:
    missing_config = make_config(tmp_path / "missing")
    missing_service = TikTokToTelegram(missing_config)
    missing_client = make_client(missing_config, missing_service)

    response = missing_client.post(
        "/setup-admin",
        json={
            "password": "admin-password",
            "confirm_password": "admin-password",
        },
    )
    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert missing_service.storage.get_user(1).must_set_password is True

    config, setup_username, setup_password = setup_config(tmp_path / "configured")
    service = TikTokToTelegram(config)
    client = make_client(config, service)
    response = client.post(
        "/setup-admin",
        json={
            "setup_username": setup_username,
            "setup_password": "wrong-password",
            "password": "admin-password",
            "confirm_password": "admin-password",
        },
    )
    assert response.status_code == 403
    assert service.storage.get_user(1).must_set_password is True

    finish_admin_setup(client, setup_username, setup_password)
    assert service.storage.get_user(1).must_set_password is False
    assert service.storage.get_user(1).password_hash


def test_login_next_rejects_open_redirects(tmp_path: Path) -> None:
    config, setup_username, setup_password = setup_config(tmp_path)
    service = TikTokToTelegram(config)
    client = make_client(config, service)
    finish_admin_setup(client, setup_username, setup_password)
    admin_username = service.storage.get_user(1).username

    for unsafe_next in (
        "https://evil.example/phishing",
        "//evil.example/phishing",
        "javascript:alert(1)",
    ):
        client.post("/logout", json={})
        response = client.post(
            "/login",
            json={
                "username": admin_username,
                "password": "admin-password",
                "next": unsafe_next,
            },
        )
        assert response.status_code == 200
        assert response.json()["redirect"] == "/"
        assert "evil.example" not in response.json()["redirect"]

    client.post("/logout", json={})
    response = client.post(
        "/login",
        json={
            "username": admin_username,
            "password": "admin-password",
            "next": "/settings",
        },
    )
    assert response.json()["redirect"] == "/settings"


def test_authenticated_bootstrap_tracks_x_and_reddit_permissions(
    tmp_path: Path,
) -> None:
    config, setup_username, setup_password = setup_config(tmp_path)
    service = TikTokToTelegram(config)
    client = make_client(config, service)
    finish_admin_setup(client, setup_username, setup_password)

    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["authenticated"] is True
    assert bootstrap["user"]["permissions"]["twitter"] is True
    assert bootstrap["user"]["permissions"]["reddit"] is True
    assert bootstrap["permissions"]["twitter"] is True
    assert bootstrap["permissions"]["reddit"] is True
    assert {"twitter", "reddit"} <= {
        item["id"] for item in bootstrap["services"]
    }

    response = client.post(
        "/admin/users/1",
        json={
            "username": service.storage.get_user(1).username,
            "is_admin": True,
            "allow_tiktok": False,
            "allow_instagram": True,
            "allow_youtube": True,
            "allow_spotify": True,
            "allow_twitter": False,
            "allow_reddit": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["permissions"]["twitter"] is False
    assert response.json()["user"]["permissions"]["reddit"] is True
    assert response.json()["user"]["allow_twitter"] is False

    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["permissions"]["tiktok"] is False
    assert bootstrap["permissions"]["twitter"] is False
    assert bootstrap["permissions"]["reddit"] is True
    for endpoint, payload in (
        ("/settings/tiktok/monitor", {"channel": "@blocked"}),
        ("/settings/tiktok/monitor/delete", {"channel": "@blocked"}),
        ("/settings/tiktok/interval", {"poll_interval_seconds": "120"}),
    ):
        denied = client.post(endpoint, json=payload)
        assert denied.status_code == 400
        assert "отключён" in denied.json()["error"]


def test_prepared_jobs_are_isolated_between_authenticated_users(
    tmp_path: Path,
) -> None:
    config, setup_username, setup_password = setup_config(tmp_path)
    service = TikTokToTelegram(config)
    client = make_client(config, service)
    finish_admin_setup(client, setup_username, setup_password)

    media_path = tmp_path / "owner-only.mp4"
    media_path.write_bytes(b"owner-only")

    def prepare_url(url: str, user_id: int = 1):
        return (
            Video(
                "owner-post",
                "owner",
                "Private prepared job",
                url,
                0,
                platform="instagram",
            ),
            (media_path,),
        )

    service.prepare_url = prepare_url
    info = client.post(
        "/media/info",
        json={"media_url": "https://www.instagram.com/reel/owner/"},
    ).json()
    assert info["job_id"]

    client.post("/logout", json={})
    assert client.post(
        "/register",
        json={
            "username": "second-user",
            "password": "second-password",
            "confirm_password": "second-password",
        },
    ).status_code == 200

    assert client.get(f"/api/media/jobs/{info['job_id']}").status_code == 404
    assert client.get(info["download_url"]).status_code == 404
    assert client.post(info["send_url"], json={}).status_code == 404
    assert client.post(info["cancel_url"], json={}).status_code == 404
    assert media_path.exists()


def test_registration_and_admin_user_mutations_use_json_contract(
    tmp_path: Path,
) -> None:
    config, setup_username, setup_password = setup_config(tmp_path)
    service = TikTokToTelegram(config)
    client = make_client(config, service)
    finish_admin_setup(client, setup_username, setup_password)
    admin_username = service.storage.get_user(1).username

    client.post("/logout", json={})
    response = client.post(
        "/register",
        json={
            "username": "alice",
            "password": "alice-password",
            "confirm_password": "alice-password",
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    alice = service.storage.get_user_by_username("alice")
    assert alice is not None
    assert client.get("/api/bootstrap").json()["user"]["username"] == "alice"

    client.post("/logout", json={})
    assert client.post(
        "/login",
        json={"username": admin_username, "password": "admin-password"},
    ).status_code == 200
    users = client.get("/api/admin/users")
    assert users.status_code == 200
    assert "alice" in {user["username"] for user in users.json()["users"]}
    admin_settings = client.get(f"/admin/users/{alice.id}/settings")
    assert admin_settings.status_code == 302
    assert admin_settings.headers["location"] == f"/settings?user_id={alice.id}"

    response = client.post(
        f"/admin/users/{alice.id}",
        json={
            "username": "alice",
            "is_admin": False,
            "is_disabled": False,
            "allow_tiktok": True,
            "allow_instagram": True,
            "allow_youtube": False,
            "allow_spotify": False,
            "allow_twitter": False,
            "allow_reddit": False,
        },
    )
    assert response.status_code == 200
    permissions = response.json()["user"]["permissions"]
    assert permissions["tiktok"] is True
    assert permissions["youtube"] is False
    assert permissions["twitter"] is False
    assert permissions["reddit"] is False

    duplicate = client.post(
        f"/admin/users/{alice.id}",
        json={
            "username": admin_username,
            "is_admin": False,
            "is_disabled": False,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "Логин уже занят"

    response = client.post(
        f"/admin/users/{alice.id}/toggle-disabled",
        json={},
    )
    assert response.status_code == 200
    assert response.json()["user"]["is_disabled"] is True
    response = client.post(
        f"/admin/users/{alice.id}/toggle-disabled",
        json={},
    )
    assert response.json()["user"]["is_disabled"] is False
