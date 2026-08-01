import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config, TelegramChannel
from app.media_sources import (
    MediaSourcePost,
    detect_media_platform,
    parse_reddit_post,
    parse_twitter_post,
    validate_reddit_url,
    validate_twitter_url,
)
from app.service import (
    SpotifyTrack,
    TikTokToTelegram,
    Video,
    YouTubeVideo,
    best_thumbnail_url,
    build_spotify_caption,
    build_youtube_caption,
    has_youtube_auth_cookies,
    has_spotify_auth_cookie,
    instagram_url_from_text,
    instagram_image_post_from_media,
    instagram_shortcode_to_media_id,
    build_caption,
    media_author_from_info,
    normalize_channel,
    processed_media_key,
    resolve_caption_html,
    username_from_info,
    validate_tiktok_url,
    validate_youtube_url,
    validate_spotify_track_url,
    is_tiktok_video_url,
    is_tiktok_photo_url,
    is_instagram_url,
    validate_instagram_url,
    sanitize_telegram_html,
    spotify_artist_from_embed_html,
    spotify_track_url_from_text,
    tiktok_image_post_urls_from_data,
)
from app.spotify_search import track_from_pathfinder, track_from_web_api


def make_service_config(tmp_path: Path) -> Config:
    return Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )


def test_normalize_channel() -> None:
    assert normalize_channel("https://www.tiktok.com/@example/") == (
        "example",
        "https://www.tiktok.com/@example",
    )
    assert normalize_channel("@example") == ("example", "https://www.tiktok.com/@example")


def test_tiktok_image_post_urls_from_data() -> None:
    data = {
        "ItemModule": {
            "123": {
                "imagePost": {
                    "images": [
                        {"imageURL": {"urlList": ["https://p16-common-sign.tiktokcdn-us.com/tos/image-one.jpeg?x=1"]}},
                        {"imageURL": {"urlList": ["https://cdn.test/two.jpeg"]}},
                        {"imageURL": {"urlList": ["https://p19-common-sign.tiktokcdn-us.com/tos/image-one.jpeg?x=2"]}},
                    ]
                }
            }
        }
    }

    assert tiktok_image_post_urls_from_data(data) == [
        "https://p16-common-sign.tiktokcdn-us.com/tos/image-one.jpeg?x=1",
        "https://cdn.test/two.jpeg",
    ]


def test_instagram_image_post_from_media_uses_best_carousel_images() -> None:
    media = {
        "code": "ABC_123",
        "caption": {"text": "Summer"},
        "taken_at": 123,
        "user": {"username": "creator.name", "full_name": "Creator"},
        "carousel_media": [
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [
                        {"url": "https://cdn.test/one-small.jpg", "width": 320, "height": 320},
                        {"url": "https://cdn.test/one.jpg", "width": 1080, "height": 1080},
                    ]
                },
            },
            {
                "media_type": 2,
                "video_versions": [{"url": "https://cdn.test/video.mp4"}],
                "image_versions2": {
                    "candidates": [{"url": "https://cdn.test/video-cover.jpg"}]
                },
            },
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": "https://cdn.test/two.jpg", "width": 1080, "height": 1350}]
                },
            },
        ],
    }

    info, image_urls = instagram_image_post_from_media(
        media, "https://www.instagram.com/p/ABC_123/"
    )

    assert info["id"] == "ABC_123"
    assert info["description"] == "Summer"
    assert info["channel"] == "creator.name"
    assert info["uploader_url"] == "https://www.instagram.com/creator.name/"
    assert image_urls == [
        "https://cdn.test/one.jpg",
        "https://cdn.test/two.jpg",
    ]


def test_instagram_shortcode_to_media_id() -> None:
    assert instagram_shortcode_to_media_id("aye83DjauH") == 482584233761418119


def test_instagram_image_info_uses_authenticated_media_api(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    service = TikTokToTelegram(config)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "items": [
                    {
                        "code": "Da20blqmA_f",
                        "media_type": 1,
                        "caption": {"text": "Photo"},
                        "user": {"username": "creator"},
                        "image_versions2": {
                            "candidates": [
                                {
                                    "url": "https://cdn.test/photo.jpg",
                                    "width": 1080,
                                    "height": 1350,
                                }
                            ]
                        },
                    }
                ]
            }

    class Session:
        headers = {}
        cookies = [SimpleNamespace(name="sessionid", value="session")]

        def get(self, url, timeout):
            assert "/api/v1/media/" in url
            return Response()

    monkeypatch.setattr(service, "_cookie_session", lambda cookies_file: Session())

    info, image_urls = service._instagram_image_post_info(
        "https://www.instagram.com/p/Da20blqmA_f/?img_index=4", None
    )

    assert info["id"] == "Da20blqmA_f"
    assert info["description"] == "Photo"
    assert image_urls == ["https://cdn.test/photo.jpg"]


def test_caption_escapes_html_and_uses_quote() -> None:
    caption = build_caption(
        Video("1", "example", "A < B & C", "https://example.test", 0)
    )
    assert '<a href="https://www.tiktok.com/@example">@example</a>' in caption
    assert "<blockquote>A &lt; B &amp; C</blockquote>" in caption
    assert len(caption) <= 1024


def test_caption_truncates_long_description() -> None:
    caption = build_caption(Video("1", "example", "x" * 2000, "https://example.test", 0))
    assert caption.endswith("…</blockquote>")
    assert len(caption) <= 1024


def test_caption_truncates_escaped_description() -> None:
    caption = build_caption(Video("1", "example", "&" * 2000, "https://example.test", 0))
    assert len(caption) <= 1024


def test_caption_supports_plain_text_before_and_after_quote() -> None:
    caption = build_caption(
        Video("1", "example", "original", "https://example.test", 0),
        "edited <quote>",
        "before & text",
        "after > text",
    )
    assert "before &amp; text" in caption
    assert "<blockquote>edited &lt;quote&gt;</blockquote>" in caption
    assert "after &gt; text" in caption
    assert caption.index("before") < caption.index("<blockquote>") < caption.index("after")


def test_caption_truncates_all_builder_sections() -> None:
    caption = build_caption(
        Video("1", "example", "original", "https://example.test", 0),
        "q" * 2000,
        "b" * 2000,
        "a" * 2000,
    )
    assert len(caption) <= 1024
    assert "<blockquote>" in caption


def test_caption_can_omit_author_and_description() -> None:
    caption = build_caption(
        Video(
            "1",
            "creator",
            "description",
            "https://instagram.com/reel/1",
            0,
            "instagram",
            "https://instagram.com/creator/",
        ),
        before_text="Only text",
        include_author=False,
        include_description=False,
    )

    assert caption == "Only text"


def test_youtube_caption_contains_link_and_custom_text() -> None:
    caption = build_youtube_caption(
        YouTubeVideo(
            "yt1",
            "Video <title>",
            "https://youtube.com/watch?v=yt1",
            "https://i.ytimg.com/yt1.jpg",
            60,
            "Channel & Co",
        ),
        "Before",
        "After",
    )
    assert "Before" in caption
    assert '<a href="https://youtube.com/watch?v=yt1">Video &lt;title&gt;</a>' in caption
    assert "Channel &amp; Co" in caption
    assert "After" in caption


def test_sanitize_telegram_html_keeps_supported_formatting() -> None:
    caption = sanitize_telegram_html(
        '<p><strong>Bold</strong> <em>Italic</em> <u>Under</u> '
        '<s>Strike</s> <tg-spoiler>Spoiler</tg-spoiler> '
        '<a href="https://example.com">Link</a></p>'
        '<blockquote expandable>Quote</blockquote><script>bad()</script>'
    )

    assert "<b>Bold</b>" in caption
    assert "<i>Italic</i>" in caption
    assert "<u>Under</u>" in caption
    assert "<s>Strike</s>" in caption
    assert "<tg-spoiler>Spoiler</tg-spoiler>" in caption
    assert '<a href="https://example.com">Link</a>' in caption
    assert "<blockquote expandable>Quote</blockquote>" in caption
    assert "<script>" not in caption


def test_empty_caption_is_not_replaced_with_fallback() -> None:
    assert resolve_caption_html(None, "Fallback") == "Fallback"
    assert resolve_caption_html("", "Fallback") == ""
    assert resolve_caption_html("<p><br></p>", "Fallback") == ""


def test_manual_url_must_be_tiktok() -> None:
    assert validate_tiktok_url("https://vm.tiktok.com/example") == "https://vm.tiktok.com/example"
    with pytest.raises(ValueError):
        validate_tiktok_url("https://example.com/video")


def test_username_prefers_canonical_url() -> None:
    info = {"uploader_id": "107955", "uploader": "TikTok"}
    assert username_from_info(info, "https://www.tiktok.com/@tiktok/video/123") == "tiktok"


def test_instagram_author_prefers_public_username_over_numeric_id() -> None:
    username, author_url = media_author_from_info(
        {
            "uploader_id": "1234567890",
            "uploader": "creator.name",
            "uploader_url": "https://www.instagram.com/creator.name/",
        },
        "https://www.instagram.com/reel/abc/",
        "instagram",
    )

    assert username == "creator.name"
    assert author_url == "https://www.instagram.com/creator.name/"


def test_tiktok_video_and_channel_detection() -> None:
    assert is_tiktok_video_url("https://www.tiktok.com/@author/video/123")
    assert is_tiktok_video_url("https://www.tiktok.com/@author/photo/123")
    assert is_tiktok_photo_url("https://www.tiktok.com/@author/photo/123")
    assert not is_tiktok_video_url("https://www.tiktok.com/@author")
    assert normalize_channel("https://www.tiktok.com/@author?lang=en")[0] == "author"


def test_manual_youtube_url_must_be_youtube() -> None:
    assert validate_youtube_url("https://youtu.be/abc") == "https://youtu.be/abc"
    with pytest.raises(ValueError):
        validate_youtube_url("https://example.com/video")


def test_manual_instagram_url_must_be_instagram() -> None:
    assert is_instagram_url("https://www.instagram.com/reel/abc/")
    assert validate_instagram_url("https://instagram.com/p/abc/").endswith("/p/abc/")
    assert not is_instagram_url("https://example.com/video")


def test_spotify_track_url_is_validated_and_normalized() -> None:
    track_id = "4uLU6hMCjMI75M1A2tKUQC"
    assert validate_spotify_track_url(
        f"https://spotify.com/intl-de/track/{track_id}?si=test"
    ) == f"https://open.spotify.com/track/{track_id}"
    with pytest.raises(ValueError):
        validate_spotify_track_url("https://open.spotify.com/album/invalid")
    with pytest.raises(ValueError):
        validate_spotify_track_url("https://example.com/track/4uLU6hMCjMI75M1A2tKUQC")


def test_spotify_artist_is_read_from_embed_metadata() -> None:
    document = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"state":{"data":{"entity":{"artists":'
        '[{"name":"First Artist"},{"name":"Second Artist"}]}}}}}}</script>'
    )

    assert spotify_artist_from_embed_html(document) == "First Artist, Second Artist"


def test_build_spotify_caption_links_track_and_artist() -> None:
    track = SpotifyTrack(
        "4uLU6hMCjMI75M1A2tKUQC",
        "Track",
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        "https://i.scdn.co/image/test.jpg",
        "Artist",
    )

    assert build_spotify_caption(track, "Before", "After") == (
        "Before\n\n"
        '<a href="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC">Track</a>'
        "\n\nArtist\n\nAfter"
    )


def test_best_thumbnail_uses_largest_resolution() -> None:
    info = {
        "thumbnail": "fallback",
        "thumbnails": [
            {"url": "small", "width": 320, "height": 180},
            {"url": "large", "width": 1280, "height": 720},
        ],
    }
    assert best_thumbnail_url(info) == "large"


def test_service_copies_cookies_to_writable_data_dir(tmp_path) -> None:
    source = tmp_path / "source-cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@channel",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=data_dir,
        cookies_file=source,
        instagram_cookies_file=source,
        youtube_cookies_file=source,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )

    service = TikTokToTelegram(config)

    assert service.tiktok_cookies_file == data_dir / "users" / "1" / "tiktok-cookies.txt"
    assert service.instagram_cookies_file == data_dir / "users" / "1" / "instagram-cookies.txt"
    assert service.youtube_cookies_file == data_dir / "users" / "1" / "youtube-cookies.txt"


def test_detects_youtube_auth_cookies(tmp_path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tvalue\n",
        encoding="utf-8",
    )
    assert has_youtube_auth_cookies(cookies)


def test_detects_spotify_auth_cookie(tmp_path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".spotify.com\tTRUE\t/\tTRUE\t0\tsp_dc\tsecret\n",
        encoding="utf-8",
    )
    assert has_spotify_auth_cookie(cookies)


def test_publish_youtube_sends_photo_to_selected_channel(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
        telegram_channels=(
            TelegramChannel("Main", "@main"),
            TelegramChannel("Second", "@second"),
        ),
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, data, timeout):
        captured.update(url=url, data=data, timeout=timeout)
        return Response()

    monkeypatch.setattr("app.service.requests.post", fake_post)
    service = TikTokToTelegram(config)
    service.storage.add_telegram_destination("Second", "@second", "second-token")
    video = YouTubeVideo(
        "yt1",
        "Title",
        "https://youtube.com/watch?v=yt1",
        "https://i.ytimg.com/yt1.jpg",
        60,
        "Channel",
    )

    service.publish_youtube(video, "Before", "After", "@second", "<b>Custom</b>")

    assert captured["url"].endswith("/sendPhoto")
    assert "botsecond-token" in captured["url"]
    assert captured["data"]["chat_id"] == "@second"
    assert captured["data"]["photo"] == video.thumbnail_url
    assert captured["data"]["caption"] == "<b>Custom</b>"


def test_publish_spotify_sends_mp3_to_selected_channel(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
        telegram_channels=(
            TelegramChannel("Main", "@main"),
            TelegramChannel("Second", "@second"),
        ),
    )
    captured = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, data, files, timeout):
        captured.append(
            {
                "url": url,
                "data": dict(data),
                "files": set(files),
                "timeout": timeout,
            }
        )
        return Response()

    class CoverResponse:
        headers = {"Content-Type": "image/jpeg"}
        content = b"jpg"

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.service.requests.post", fake_post)
    monkeypatch.setattr(
        "app.service.requests.get", lambda *args, **kwargs: CoverResponse()
    )
    service = TikTokToTelegram(config)
    service.storage.add_telegram_destination("Second", "@second", "second-token")
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"mp3")
    track = SpotifyTrack(
        "4uLU6hMCjMI75M1A2tKUQC",
        "Track",
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        "https://i.scdn.co/image/test.jpg",
        "Artist",
    )

    service.publish_spotify(
        track,
        audio,
        chat_id="@second",
        caption_html="<b>Custom</b>",
    )

    photo_call, audio_call = captured
    assert photo_call["url"].endswith("/sendPhoto")
    assert "botsecond-token" in photo_call["url"]
    assert photo_call["data"]["chat_id"] == "@second"
    assert photo_call["data"]["caption"] == "<b>Custom</b>"
    assert photo_call["files"] == {"photo"}

    assert audio_call["url"].endswith("/sendAudio")
    assert "botsecond-token" in audio_call["url"]
    assert audio_call["data"]["chat_id"] == "@second"
    assert audio_call["data"]["caption"] == ""
    assert audio_call["data"]["title"] == "Track"
    assert audio_call["data"]["performer"] == "Artist"
    assert audio_call["files"] == {"audio", "thumbnail"}


def test_spotify_track_url_can_be_read_from_command_or_reply() -> None:
    url = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=test"
    normalized = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"

    assert spotify_track_url_from_text(f"/spotify {url}") == normalized
    assert spotify_track_url_from_text(f"Послушай: {url}.") == normalized
    with pytest.raises(ValueError, match="Добавьте ссылку"):
        spotify_track_url_from_text("/spotify")


def test_instagram_url_can_be_read_from_command_or_reply() -> None:
    url = "https://www.instagram.com/p/ABC_123/?igsh=test"

    assert instagram_url_from_text(f"/instagram {url}") == url
    assert instagram_url_from_text(f"Посмотри {url}.") == url
    with pytest.raises(ValueError, match="Добавьте ссылку"):
        instagram_url_from_text("/instagram")


def test_spotify_search_payload_uses_first_track_and_largest_cover() -> None:
    track_data = {
        "id": "6VK8OMA2FhX4KoS3QCH7rL",
        "name": "Aria Math",
        "artists": {
            "items": [
                {"profile": {"name": "C418"}},
                {"profile": {"name": "Second Artist"}},
            ]
        },
        "albumOfTrack": {
            "coverArt": {
                "sources": [
                    {"url": "https://i.scdn.co/small.jpg", "width": 64, "height": 64},
                    {"url": "https://i.scdn.co/large.jpg", "width": 640, "height": 640},
                ]
            }
        },
    }
    pathfinder = {
        "data": {
            "searchV2": {
                "tracksV2": {"items": [{"item": {"data": track_data}}]}
            }
        }
    }
    web_api = {
        "tracks": {
            "items": [
                {
                    "id": track_data["id"],
                    "name": track_data["name"],
                    "artists": [{"name": "C418"}],
                    "album": {
                        "images": [
                            {
                                "url": "https://i.scdn.co/large.jpg",
                                "width": 640,
                                "height": 640,
                            }
                        ]
                    },
                }
            ]
        }
    }

    result = track_from_pathfinder(pathfinder)

    assert result == {
        "track_id": "6VK8OMA2FhX4KoS3QCH7rL",
        "title": "Aria Math",
        "artist": "C418, Second Artist",
        "thumbnail_url": "https://i.scdn.co/large.jpg",
        "url": "https://open.spotify.com/track/6VK8OMA2FhX4KoS3QCH7rL",
    }
    assert track_from_web_api(web_api)["artist"] == "C418"


def test_service_searches_spotify_with_authenticated_catalog(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    cookies = tmp_path / "users" / "1" / "spotify-cookies.txt"
    cookies.parent.mkdir(parents=True, exist_ok=True)
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".spotify.com\tTRUE\t/\tTRUE\t0\tsp_dc\tsecret\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "track_id": "6VK8OMA2FhX4KoS3QCH7rL",
                    "title": "Aria Math",
                    "artist": "C418",
                    "thumbnail_url": "https://i.scdn.co/cover.jpg",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.service.subprocess.run", fake_run)

    track = service.search_spotify("  aria   math  ")

    assert captured["command"][1:3] == ["-m", "app.spotify_search"]
    assert captured["command"][-2] == "aria math"
    assert track == SpotifyTrack(
        "6VK8OMA2FhX4KoS3QCH7rL",
        "Aria Math",
        "https://open.spotify.com/track/6VK8OMA2FhX4KoS3QCH7rL",
        "https://i.scdn.co/cover.jpg",
        "C418",
    )


def test_telegram_command_menu_contains_all_download_sources(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    captured = {}

    class Response:
        def json(self):
            return {"ok": True, "result": True}

    def fake_post(url, data, timeout):
        captured.update(url=url, data=dict(data), timeout=timeout)
        return Response()

    monkeypatch.setattr("app.service.requests.post", fake_post)

    service._ensure_telegram_commands("token", 1)

    commands = json.loads(captured["data"]["commands"])
    assert [item["command"] for item in commands] == [
        "spotify",
        "spotifysearch",
        "instagram",
        "x",
        "reddit",
    ]


def test_telegram_spotify_command_rejects_unregistered_chat(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    calls: list[tuple] = []

    class Response:
        def json(self):
            return {"ok": True, "result": {"message_id": 73}}

    def fake_post(url, data, timeout):
        calls.append((url.rsplit("/", 1)[-1], dict(data)))
        return Response()

    monkeypatch.setattr("app.service.requests.post", fake_post)
    monkeypatch.setattr(
        service,
        "get_spotify_info",
        lambda *args, **kwargs: pytest.fail("unregistered command must not run"),
    )

    service.process_telegram_update(
        {
            "message": {
                "text": (
                    "/spotify "
                    "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
                ),
                "chat": {
                    "id": -100999,
                    "title": "Music chat",
                    "type": "supergroup",
                },
                "from": {"id": 42},
                "message_thread_id": 17,
            }
        },
        "command-token",
        1,
    )

    assert "-100999" not in {
        destination.chat_id
        for destination in service.storage.telegram_destinations()
    }
    assert calls == [
        (
        "sendMessage",
        {
            "chat_id": "-100999",
            "text": "Сначала добавьте чат в настройках ClipRelay",
            "message_thread_id": 17,
        },
        )
    ]


def test_telegram_membership_update_does_not_register_chat(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    monkeypatch.setattr(
        "app.service.requests.post",
        lambda *args, **kwargs: pytest.fail("membership update must be ignored"),
    )

    service.process_telegram_update(
        {
            "my_chat_member": {
                "chat": {
                    "id": -100999,
                    "title": "Music chat",
                    "type": "supergroup",
                },
                "new_chat_member": {"status": "administrator"},
            }
        },
        "command-token",
        1,
    )

    assert "-100999" not in {
        destination.chat_id
        for destination in service.storage.telegram_destinations()
    }


def test_telegram_spotify_command_reads_link_from_replied_message(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    service.storage.add_telegram_destination(
        "Private chat", "123", "token", destination_type="private"
    )
    captured = {}
    track = SpotifyTrack(
        "4uLU6hMCjMI75M1A2tKUQC",
        "Track",
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        "",
        "Artist",
    )
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"mp3")

    class Response:
        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(
        "app.service.requests.post", lambda *args, **kwargs: Response()
    )
    monkeypatch.setattr(
        service,
        "get_spotify_info",
        lambda url, user_id=1: captured.setdefault("url", url) and track,
    )
    monkeypatch.setattr(
        service, "download_spotify", lambda track_arg, output_id, user_id=1: audio
    )
    monkeypatch.setattr(
        service, "_publish_spotify_to_telegram", lambda *args, **kwargs: None
    )

    service.process_telegram_update(
        {
            "message": {
                "text": "/spotify",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 42},
                "reply_to_message": {
                    "text": (
                        "https://open.spotify.com/track/"
                        "4uLU6hMCjMI75M1A2tKUQC?si=reply"
                    )
                },
            }
        },
        "token",
        1,
    )

    assert captured["url"] == (
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
    )


def test_telegram_spotify_search_finds_and_publishes_track(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    service.storage.add_telegram_destination(
        "Private chat", "123", "command-token", destination_type="private"
    )
    audio = tmp_path / "aria-math.mp3"
    audio.write_bytes(b"mp3")
    track = SpotifyTrack(
        "6VK8OMA2FhX4KoS3QCH7rL",
        "Aria Math",
        "https://open.spotify.com/track/6VK8OMA2FhX4KoS3QCH7rL",
        "https://i.scdn.co/cover.jpg",
        "C418",
    )
    calls = []
    captured = {}

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_post(url, data, timeout):
        calls.append((url.rsplit("/", 1)[-1], dict(data)))
        if url.endswith("/sendMessage"):
            return Response({"ok": True, "result": {"message_id": 75}})
        return Response({"ok": True, "result": True})

    def fake_search(query, user_id=1):
        captured.update(query=query, user_id=user_id)
        return track

    def fake_publish(track_arg, path, bot_token, chat_id, **kwargs):
        captured.update(
            track=track_arg,
            path=path,
            bot_token=bot_token,
            chat_id=chat_id,
            kwargs=kwargs,
        )

    monkeypatch.setattr("app.service.requests.post", fake_post)
    monkeypatch.setattr(service, "search_spotify", fake_search)
    monkeypatch.setattr(
        service, "download_spotify", lambda track_arg, output_id, user_id=1: audio
    )
    monkeypatch.setattr(service, "_publish_spotify_to_telegram", fake_publish)

    service.process_telegram_update(
        {
            "message": {
                "text": "/spotifysearch aria math c418",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 42},
                "message_thread_id": 19,
            }
        },
        "command-token",
        1,
    )

    assert captured == {
        "query": "aria math c418",
        "user_id": 1,
        "track": track,
        "path": audio,
        "bot_token": "command-token",
        "chat_id": "123",
        "kwargs": {"caption_html": None, "message_thread_id": 19},
    }
    assert calls[0] == (
        "sendMessage",
        {
            "chat_id": "123",
            "text": "Ищу трек в Spotify…",
            "message_thread_id": 19,
        },
    )
    assert calls[1] == (
        "editMessageText",
        {
            "chat_id": "123",
            "message_id": 75,
            "text": "Нашёл: Aria Math — C418\nГотовлю MP3 320 кбит/с…",
        },
    )
    assert calls[-1] == (
        "deleteMessage",
        {"chat_id": "123", "message_id": 75},
    )


def test_telegram_instagram_command_publishes_carousel_and_cleans_files(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    service.storage.add_telegram_destination(
        "Private chat", "123", "command-token", destination_type="private"
    )
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    post = Video(
        "ABC_123",
        "creator",
        "Описание поста",
        "https://www.instagram.com/p/ABC_123/",
        0,
        platform="instagram",
        author_url="https://www.instagram.com/creator/",
        media_type="image",
    )
    calls = []
    published = {}

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_post(url, data, timeout):
        calls.append((url.rsplit("/", 1)[-1], dict(data)))
        if url.endswith("/sendMessage"):
            return Response({"ok": True, "result": {"message_id": 74}})
        return Response({"ok": True, "result": True})

    def fake_prepare(url, user_id=1):
        assert url == "https://www.instagram.com/p/ABC_123/?igsh=test"
        assert user_id == 1
        return post, (first, second)

    def fake_publish(video, paths, bot_token, chat_id, caption, **kwargs):
        published.update(
            video=video,
            paths=paths,
            bot_token=bot_token,
            chat_id=chat_id,
            caption=caption,
            kwargs=kwargs,
        )

    monkeypatch.setattr("app.service.requests.post", fake_post)
    monkeypatch.setattr(service, "prepare_url", fake_prepare)
    monkeypatch.setattr(service, "_publish_media_to_telegram", fake_publish)

    service.process_telegram_update(
        {
            "message": {
                "text": (
                    "/instagram "
                    "https://www.instagram.com/p/ABC_123/?igsh=test"
                ),
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 42},
                "message_thread_id": 18,
            }
        },
        "command-token",
        1,
    )

    assert published["video"] == post
    assert published["paths"] == (first, second)
    assert published["bot_token"] == "command-token"
    assert published["chat_id"] == "123"
    assert "creator" in published["caption"]
    assert "Описание поста" in published["caption"]
    assert published["kwargs"] == {"message_thread_id": 18}
    assert not first.exists()
    assert not second.exists()
    assert calls[0] == (
        "sendMessage",
        {
            "chat_id": "123",
            "text": "Скачиваю Instagram-пост…",
            "message_thread_id": 18,
        },
    )
    assert calls[-1] == (
        "deleteMessage",
        {"chat_id": "123", "message_id": 74},
    )


def test_telegram_command_poll_persists_update_offset(tmp_path, monkeypatch) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    captured = {}

    class Response:
        def json(self):
            return {
                "ok": True,
                "result": [{"update_id": 91, "message": {"text": "обычный текст"}}],
            }

    monkeypatch.setattr(service, "_telegram_bot_owners", lambda: {"token": 1})
    monkeypatch.setattr(
        service, "_ensure_telegram_commands", lambda token, user_id: None
    )

    def fake_get(url, params, timeout):
        captured.update(url=url, params=dict(params), timeout=timeout)
        return Response()

    monkeypatch.setattr("app.service.requests.get", fake_get)

    assert service.poll_telegram_commands_once() == 1
    assert captured["params"]["offset"] == 0
    assert '"message"' in captured["params"]["allowed_updates"]
    assert service.storage.setting("telegram_spotify_offset_token", "0") == "92"


def test_publish_tiktok_image_post_sends_media_group(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
        telegram_channels=(
            TelegramChannel("Main", "@main"),
            TelegramChannel("Second", "@second"),
        ),
    )
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, data, files, timeout):
        captured.update(url=url, data=data, files=set(files), timeout=timeout)
        return Response()

    monkeypatch.setattr("app.service.requests.post", fake_post)
    service = TikTokToTelegram(config)
    service.storage.add_telegram_destination("Second", "@second", "second-token")
    video = Video(
        "tt1",
        "author",
        "Caption",
        "https://www.tiktok.com/@author/video/tt1",
        0,
        media_type="image",
    )

    service.publish(video, (first, second), chat_id="@second")

    assert captured["url"].endswith("/sendMediaGroup")
    assert captured["data"]["chat_id"] == "@second"
    media = json.loads(captured["data"]["media"])
    assert [item["type"] for item in media] == ["photo", "photo"]
    assert media[0]["caption"]
    assert media[0]["parse_mode"] == "HTML"
    assert captured["files"] == {"photo0", "photo1"}


def test_download_images_skips_duplicate_signed_urls(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    service = TikTokToTelegram(config)
    requested: list[str] = []

    class Response:
        headers = {"Content-Type": "image/jpeg"}
        content = b"image"

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, timeout):
            requested.append(url)
            return Response()

    monkeypatch.setattr(service, "_cookie_session", lambda cookies_file=None: Session())

    paths = service._download_images(
        [
            "https://p16-common-sign.tiktokcdn-us.com/tos/same.jpeg?x=1",
            "https://p19-common-sign.tiktokcdn-us.com/tos/same.jpeg?x=2",
            "https://p16-common-sign.tiktokcdn-us.com/tos/other.jpeg?x=3",
        ],
        "post",
        None,
    )

    assert len(paths) == 2
    assert requested == [
        "https://p16-common-sign.tiktokcdn-us.com/tos/same.jpeg?x=1",
        "https://p16-common-sign.tiktokcdn-us.com/tos/other.jpeg?x=3",
    ]


def test_prepare_tiktok_photo_url_without_ytdlp(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    service = TikTokToTelegram(config)

    def fail_extract_info(*args, **kwargs):
        raise AssertionError("yt-dlp must not be used for /photo/ URLs")

    monkeypatch.setattr(service, "_extract_info", fail_extract_info)
    monkeypatch.setattr(
        service,
        "_tiktok_image_post_urls",
        lambda url, cookies_file: ["https://cdn.test/image.jpg"],
    )
    monkeypatch.setattr(
        service,
        "_download_images",
        lambda urls, output_id, cookies_file: (image_path,),
    )

    video, paths = service.prepare_url(
        "https://www.tiktok.com/@knopkot_tiktok/photo/7653583264212471048"
    )

    assert video.video_id == "7653583264212471048"
    assert video.username == "knopkot_tiktok"
    assert video.media_type == "image"
    assert paths == (image_path,)


def test_prepare_tiktok_photo_url_falls_back_to_tikwm(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    service = TikTokToTelegram(config)

    monkeypatch.setattr(service, "_tiktok_image_post_urls", lambda url, cookies_file: [])
    monkeypatch.setattr(
        service,
        "_tikwm_image_post_info",
        lambda url: (
            {
                "id": "7653587728289877255",
                "description": "Forest",
                "uploader": "knopkot_tiktok",
                "webpage_url": url,
            },
            ["https://cdn.test/image.jpg"],
        ),
    )
    monkeypatch.setattr(
        service,
        "_download_images",
        lambda urls, output_id, cookies_file: (image_path,),
    )

    video, paths = service.prepare_url(
        "https://www.tiktok.com/@knopkot_tiktok/photo/7653587728289877255"
    )

    assert video.video_id == "7653587728289877255"
    assert video.username == "knopkot_tiktok"
    assert video.description == "Forest"
    assert video.media_type == "image"
    assert paths == (image_path,)


def test_prepare_instagram_image_post_without_video_download(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    service = TikTokToTelegram(config)

    monkeypatch.setattr(
        service,
        "_instagram_image_post_info",
        lambda url, cookies_file: (
            {
                "id": "ABC_123",
                "description": "Summer",
                "channel": "creator.name",
                "webpage_url": url,
            },
            ["https://cdn.test/one.jpg", "https://cdn.test/two.jpg"],
        ),
    )
    monkeypatch.setattr(
        service,
        "_download_images",
        lambda urls, output_id, cookies_file: (first, second),
    )

    def fail_video(*args, **kwargs):
        raise AssertionError("Video downloader must not be used for an Instagram image post")

    monkeypatch.setattr(service, "_extract_info", fail_video)
    monkeypatch.setattr(service, "_download_video_file", fail_video)

    video, paths = service.prepare_url("https://www.instagram.com/p/ABC_123/")

    assert video.video_id == "ABC_123"
    assert video.username == "creator.name"
    assert video.description == "Summer"
    assert video.platform == "instagram"
    assert video.media_type == "image"
    assert paths == (first, second)


def test_prepare_instagram_video_post_keeps_video_download(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    service = TikTokToTelegram(config)
    info = {
        "id": "VIDEO_123",
        "description": "Clip",
        "channel": "creator.name",
        "webpage_url": "https://www.instagram.com/p/VIDEO_123/",
    }

    monkeypatch.setattr(service, "_instagram_image_post_info", lambda url, cookies: ({}, []))
    monkeypatch.setattr(service, "_extract_info", lambda url, platform, user_id: info)
    monkeypatch.setattr(
        service,
        "_download_video_file",
        lambda url, platform, output_id, user_id: (info, video_path),
    )

    video, paths = service.prepare_url("https://www.instagram.com/p/VIDEO_123/")

    assert video.video_id == "VIDEO_123"
    assert video.platform == "instagram"
    assert video.media_type == "video"
    assert paths == (video_path,)


def test_service_updates_uploaded_cookies_without_restart(tmp_path) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    service = TikTokToTelegram(config)

    path = service.update_cookies("instagram", b"# Netscape HTTP Cookie File\n")

    assert path == tmp_path / "users" / "1" / "instagram-cookies.txt"
    assert service.instagram_cookies_file == path
    assert path.read_bytes() == b"# Netscape HTTP Cookie File\n"
    assert service.cookie_status("instagram") == {
        "uploaded": True,
        "valid": False,
        "reason": "empty",
        "cookie_count": 0,
    }


def test_service_requires_sp_dc_in_uploaded_spotify_cookies(tmp_path) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    service = TikTokToTelegram(config)

    with pytest.raises(ValueError, match="sp_dc"):
        service.update_cookies(
            "spotify",
            b"# Netscape HTTP Cookie File\n.spotify.com\tTRUE\t/\tTRUE\t0\tsp_key\tvalue\n",
        )

    path = service.update_cookies(
        "spotify",
        b"# Netscape HTTP Cookie File\n.spotify.com\tTRUE\t/\tTRUE\t0\tsp_dc\tsecret\n",
    )
    assert path == tmp_path / "users" / "1" / "spotify-cookies.txt"
    assert has_spotify_auth_cookie(path)


@pytest.mark.parametrize(
    ("service_name", "auth_cookie_name"),
    [
        ("tiktok", "sessionid"),
        ("instagram", "sessionid"),
        ("youtube", "LOGIN_INFO"),
        ("spotify", "sp_dc"),
        ("twitter", "auth_token"),
        ("reddit", "reddit_session"),
    ],
)
def test_cookie_status_recognizes_active_service_auth_cookies(
    tmp_path, service_name, auth_cookie_name
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    service.update_cookies(
        service_name,
        (
            "# Netscape HTTP Cookie File\n"
            f".example.com\tTRUE\t/\tTRUE\t0\t{auth_cookie_name}\tsecret\n"
        ).encode(),
    )

    assert service.cookie_status(service_name) == {
        "uploaded": True,
        "valid": True,
        "reason": "ready",
        "cookie_count": 1,
    }


def test_cookie_status_reports_missing_malformed_expired_and_non_auth_files(
    tmp_path,
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))

    assert service.cookie_status("twitter") == {
        "uploaded": False,
        "valid": False,
        "reason": "missing",
        "cookie_count": 0,
    }

    service.update_cookies(
        "twitter",
        b"# Netscape HTTP Cookie File\nnot-a-cookie\n",
    )
    assert service.cookie_status("twitter")["reason"] == "invalid_format"

    service.update_cookies(
        "twitter",
        b"# Netscape HTTP Cookie File\n"
        b".x.com\tTRUE\t/\tTRUE\t1\tauth_token\tsecret\n",
    )
    assert service.cookie_status("twitter") == {
        "uploaded": True,
        "valid": False,
        "reason": "expired",
        "cookie_count": 0,
    }

    service.update_cookies(
        "twitter",
        b"# Netscape HTTP Cookie File\n"
        b".x.com\tTRUE\t/\tTRUE\t0\tct0\tcsrf-token\n",
    )
    assert service.cookie_status("twitter") == {
        "uploaded": True,
        "valid": False,
        "reason": "missing_auth_cookie",
        "cookie_count": 1,
    }


def test_service_startup_keeps_active_spotify_download_directory(tmp_path) -> None:
    active_download = tmp_path / "downloads" / "spotify-active"
    active_download.mkdir(parents=True)
    source = active_download / "source.ogg"
    source.write_bytes(b"active")
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )

    TikTokToTelegram(config)

    assert source.read_bytes() == b"active"


def test_spotify_download_uses_direct_high_quality_stream_and_mp3_320(
    tmp_path, monkeypatch
) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )
    service = TikTokToTelegram(config)
    service.update_cookies(
        "spotify",
        b"# Netscape HTTP Cookie File\n.spotify.com\tTRUE\t/\tTRUE\t0\tsp_dc\tsecret\n",
    )
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["-m", "app.spotify_auth"]:
            Path(command[-1]).write_text(
                '{"access_token":"token","premium":true}',
                encoding="utf-8",
            )
        elif command[1:3] == ["-m", "app.spotify_player"]:
            Path(command[-1]).write_bytes(b"ogg")
        else:
            Path(command[-1]).write_bytes(b"mp3")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    class CoverResponse:
        headers = {"Content-Type": "image/jpeg"}
        content = b"jpg"

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.service.subprocess.run", fake_run)
    monkeypatch.setattr("app.service.requests.get", lambda *args, **kwargs: CoverResponse())
    track = SpotifyTrack(
        "4uLU6hMCjMI75M1A2tKUQC",
        "Track",
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        "https://i.scdn.co/image/test.jpg",
    )

    result = service.download_spotify(track, "spotify-test")

    assert result.read_bytes() == b"mp3"
    auth_command, player_command, ffmpeg_command = commands
    assert auth_command[1:3] == ["-m", "app.spotify_auth"]
    assert player_command[1:3] == ["-m", "app.spotify_player"]
    assert player_command[-2] == track.track_id
    assert ["-b:a", "320k"] == ffmpeg_command[
        ffmpeg_command.index("-b:a") : ffmpeg_command.index("-b:a") + 2
    ]
    assert "attached_pic" in ffmpeg_command


def test_service_accepts_private_telegram_channel_id(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )

    class Response:
        def json(self):
            return {"ok": True}

    captured = {}

    def fake_get(url, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return Response()

    monkeypatch.setattr("app.service.requests.get", fake_get)
    service = TikTokToTelegram(config)

    service.add_telegram_destination("Private", "-1001234567890", "private-token")

    assert captured["params"]["chat_id"] == "-1001234567890"
    assert service.storage.telegram_destination("-1001234567890").name == "Private"


def test_service_discovers_private_channel_from_bot_updates(tmp_path, monkeypatch) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )

    class Response:
        def json(self):
            return {
                "ok": True,
                "result": [
                    {
                        "channel_post": {
                            "chat": {
                                "id": -1001234567890,
                                "title": "Private channel",
                                "type": "channel",
                            }
                        }
                    }
                ],
            }

    monkeypatch.setattr("app.service.requests.get", lambda *args, **kwargs: Response())
    service = TikTokToTelegram(config)

    found = service.discover_telegram_destinations("private-token")

    assert found == (TelegramChannel("Private channel", "-1001234567890"),)
    destination = service.storage.telegram_destination("-1001234567890")
    assert destination.name == "Private channel"
    assert destination.bot_token == "private-token"


def test_service_discovers_public_channel_by_username_without_duplicate(
    tmp_path, monkeypatch
) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@public",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )

    class Response:
        def json(self):
            return {
                "ok": True,
                "result": [
                    {
                        "channel_post": {
                            "chat": {
                                "id": -1001234567890,
                                "title": "Public channel",
                                "username": "public",
                                "type": "channel",
                            }
                        }
                    }
                ],
            }

    monkeypatch.setattr("app.service.requests.get", lambda *args, **kwargs: Response())
    service = TikTokToTelegram(config)
    service.storage.add_telegram_destination("Old duplicate", "-1001234567890", "token")

    found = service.discover_telegram_destinations("new-token")

    assert found == (TelegramChannel("Public channel", "@public"),)
    destinations = service.storage.telegram_destinations()
    assert [item.chat_id for item in destinations] == ["@public"]
    assert destinations[0].bot_token == "new-token"


def test_service_replaces_old_public_username_after_channel_tag_change(
    tmp_path, monkeypatch
) -> None:
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id="@main",
        tiktok_channels=(),
        poll_interval_seconds=300,
        scan_limit=15,
        post_existing=False,
        data_dir=tmp_path,
        cookies_file=None,
        instagram_cookies_file=None,
        youtube_cookies_file=None,
        youtube_po_token_provider_url=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_username=None,
        web_password=None,
    )

    class UpdatesResponse:
        def json(self):
            return {
                "ok": True,
                "result": [
                    {
                        "channel_post": {
                            "chat": {
                                "id": -1001234567890,
                                "title": "Public channel",
                                "username": "old_public",
                                "type": "channel",
                            }
                        }
                    }
                ],
            }

    class ChatResponse:
        def json(self):
            return {
                "ok": True,
                "result": {
                    "id": -1001234567890,
                    "title": "Public channel",
                    "username": "new_public",
                    "type": "channel",
                },
            }

    def fake_get(url, *args, **kwargs):
        return UpdatesResponse() if url.endswith("/getUpdates") else ChatResponse()

    monkeypatch.setattr("app.service.requests.get", fake_get)
    service = TikTokToTelegram(config)
    service.storage.add_telegram_destination("Public channel", "@old_public", "old-token")

    found = service.discover_telegram_destinations("new-token")

    assert found == (TelegramChannel("Public channel", "@new_public"),)
    destinations = service.storage.telegram_destinations()
    by_chat_id = {item.chat_id: item for item in destinations}
    assert "@old_public" not in by_chat_id
    assert by_chat_id["@new_public"].telegram_id == "-1001234567890"


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://x.com/creator/status/1900000000000000001", "twitter"),
        ("https://x.com/i/status/1900000000000000003", "twitter"),
        ("https://twitter.com/i/web/status/1900000000000000002", "twitter"),
        (
            "https://www.reddit.com/r/python/comments/abc123/a_useful_post/",
            "reddit",
        ),
        (
            "https://www.reddit.com/r/python/comments/abc123/a_post/comment42/",
            "reddit",
        ),
        ("https://www.reddit.com/r/pics/gallery/abc123", "reddit"),
        ("https://redd.it/abc123", "reddit"),
    ],
)
def test_social_url_registry_detects_supported_posts(url, platform) -> None:
    assert detect_media_platform(url) == platform


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/creator",
        "https://x.com/creator/status/not-a-number",
        "https://x.com.evil.test/creator/status/1900000000000000001",
    ],
)
def test_twitter_url_validation_rejects_non_posts(url) -> None:
    with pytest.raises(ValueError):
        validate_twitter_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.reddit.com/r/python/",
        "https://i.redd.it/image.jpg",
        "https://reddit.com.evil.test/r/python/comments/abc123/post/",
    ],
)
def test_reddit_url_validation_rejects_non_posts(url) -> None:
    with pytest.raises(ValueError):
        validate_reddit_url(url)


def test_parse_twitter_photo_post_is_pure_and_preserves_metadata() -> None:
    post = parse_twitter_post(
        {
            "id_str": "1900000000000000001",
            "full_text": "Photo caption",
            "created_at": "Sat Aug 01 10:00:00 +0000 2026",
            "user": {"screen_name": "creator"},
            "mediaDetails": [
                {
                    "type": "photo",
                    "media_url_https": "https://pbs.twimg.com/media/one.jpg?format=jpg",
                },
                {
                    "type": "photo",
                    "media_url_https": "https://pbs.twimg.com/media/two.jpg",
                },
            ],
        },
        "https://x.com/creator/status/1900000000000000001",
    )

    assert post.post_id == "1900000000000000001"
    assert post.username == "creator"
    assert post.description == "Photo caption"
    assert post.platform == "twitter"
    assert post.author_url == "https://x.com/creator"
    assert post.media_type == "image"
    assert len(post.image_urls) == 2
    assert all("name=orig" in url for url in post.image_urls)


@pytest.mark.parametrize(
    ("media_details", "expected_type"),
    [
        ([], "text"),
        ([{"type": "video", "video_info": {"variants": []}}], "video"),
    ],
)
def test_parse_twitter_text_and_video_posts(media_details, expected_type) -> None:
    post = parse_twitter_post(
        {
            "id_str": "1900000000000000002",
            "text": "Post body",
            "user": {"screen_name": "creator"},
            "mediaDetails": media_details,
        },
        "https://twitter.com/creator/status/1900000000000000002",
    )

    assert post.media_type == expected_type
    assert post.description == "Post body"


def test_parse_reddit_gallery_post_preserves_order_and_metadata() -> None:
    post = parse_reddit_post(
        [
            {
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "abc123",
                                "author": "poster",
                                "title": "Gallery title",
                                "selftext": "Gallery body",
                                "created_utc": 1785578400,
                                "permalink": "/r/pics/comments/abc123/gallery_title/",
                                "gallery_data": {
                                    "items": [
                                        {"media_id": "second"},
                                        {"media_id": "first"},
                                    ]
                                },
                                "media_metadata": {
                                    "first": {
                                        "e": "Image",
                                        "s": {"u": "https://i.redd.it/first.jpg"},
                                    },
                                    "second": {
                                        "e": "Image",
                                        "s": {
                                            "u": "https://preview.redd.it/second.jpg?a=1&amp;b=2"
                                        },
                                    },
                                },
                            }
                        }
                    ]
                }
            }
        ],
        "https://www.reddit.com/gallery/abc123",
    )

    assert post.post_id == "abc123"
    assert post.username == "poster"
    assert post.description == "Gallery title\n\nGallery body"
    assert post.webpage_url == (
        "https://www.reddit.com/r/pics/comments/abc123/gallery_title/"
    )
    assert post.author_url == "https://www.reddit.com/user/poster/"
    assert post.media_type == "image"
    assert post.image_urls == (
        "https://preview.redd.it/second.jpg?a=1&b=2",
        "https://i.redd.it/first.jpg",
    )


def test_parse_reddit_single_image_and_text_posts() -> None:
    image = parse_reddit_post(
        {
            "id": "image1",
            "author": "poster",
            "title": "Image title",
            "post_hint": "image",
            "url_overridden_by_dest": "https://i.redd.it/image1.png",
        },
        "https://redd.it/image1",
    )
    text_post = parse_reddit_post(
        {
            "id": "text1",
            "author": "writer",
            "title": "Question",
            "selftext": "Long-form answer",
        },
        "https://www.reddit.com/r/test/comments/text1/question/",
    )

    assert image.media_type == "image"
    assert image.image_urls == ("https://i.redd.it/image1.png",)
    assert text_post.media_type == "text"
    assert text_post.description == "Question\n\nLong-form answer"
    assert text_post.image_urls == ()


def test_parse_reddit_crosspost_uses_original_video_metadata() -> None:
    post = parse_reddit_post(
        {
            "id": "wrapper1",
            "author": "crossposter",
            "title": "Crosspost",
            "url_overridden_by_dest": (
                "https://www.reddit.com/r/videos/comments/original/video/"
            ),
            "crosspost_parent_list": [
                {
                    "id": "original",
                    "is_video": True,
                    "secure_media": {
                        "reddit_video": {
                            "fallback_url": "https://v.redd.it/video/DASH_720.mp4"
                        }
                    },
                }
            ],
        },
        "https://www.reddit.com/r/test/comments/wrapper1/crosspost/",
    )

    assert post.post_id == "wrapper1"
    assert post.username == "crossposter"
    assert post.media_type == "video"


def test_prepare_twitter_photo_and_text_posts_without_real_network(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    image_path = tmp_path / "tweet.jpg"
    image_path.write_bytes(b"image")
    photo_post = MediaSourcePost(
        "1900000000000000001",
        "creator",
        "Photo caption",
        "https://x.com/creator/status/1900000000000000001",
        1785578400,
        "twitter",
        "https://x.com/creator",
        "image",
        ("https://pbs.twimg.com/media/photo.jpg?name=orig",),
    )
    text_post = MediaSourcePost(
        "1900000000000000002",
        "writer",
        "A text-only post",
        "https://x.com/writer/status/1900000000000000002",
        1785578401,
        "twitter",
        "https://x.com/writer",
        "text",
    )
    posts = iter((photo_post, text_post))
    downloaded = {}

    monkeypatch.setattr(
        service,
        "_social_post_info",
        lambda url, platform, user_id: next(posts),
    )

    def fake_download_images(urls, output_id, cookies_file):
        downloaded.update(
            urls=urls,
            output_id=output_id,
            cookies_file=cookies_file,
        )
        return (image_path,)

    monkeypatch.setattr(service, "_download_images", fake_download_images)

    photo, photo_paths = service.prepare_url(photo_post.webpage_url)
    text, text_paths = service.prepare_url(text_post.webpage_url)

    assert photo == service._video_from_source_post(photo_post)
    assert photo_paths == (image_path,)
    assert downloaded["urls"] == list(photo_post.image_urls)
    assert downloaded["output_id"].startswith("manual-")
    assert downloaded["cookies_file"] is None
    assert text == service._video_from_source_post(text_post)
    assert text.media_type == "text"
    assert text_paths == ()


def test_prepare_reddit_video_keeps_all_downloaded_files(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    source_post = MediaSourcePost(
        "abc123",
        "poster",
        "Two clips",
        "https://www.reddit.com/r/videos/comments/abc123/two_clips/",
        1785578400,
        "reddit",
        "https://www.reddit.com/user/poster/",
        "video",
    )
    captured = {}

    monkeypatch.setattr(
        service,
        "_social_post_info",
        lambda url, platform, user_id: source_post,
    )

    def fake_download(url, platform, output_id, user_id):
        captured.update(
            url=url,
            platform=platform,
            output_id=output_id,
            user_id=user_id,
        )
        return {"id": source_post.post_id}, (first, second)

    monkeypatch.setattr(service, "_download_video_files", fake_download)

    video, paths = service.prepare_url(source_post.webpage_url)

    assert video == service._video_from_source_post(source_post)
    assert paths == (first, second)
    assert captured["platform"] == "reddit"
    assert captured["output_id"].startswith("manual-")


def test_twitter_metadata_falls_back_to_uploaded_cookies(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    service.update_cookies(
        "twitter",
        (
            b"# Netscape HTTP Cookie File\n"
            b".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tsecret\n"
        ),
    )
    expected = MediaSourcePost(
        "1900000000000000001",
        "creator",
        "Private post",
        "https://x.com/creator/status/1900000000000000001",
        0,
        "twitter",
        "https://x.com/creator",
        "text",
    )
    monkeypatch.setattr(
        "app.service.fetch_twitter_syndication_post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("restricted")),
    )
    monkeypatch.setattr(
        service,
        "_twitter_authenticated_post_info",
        lambda url, user_id: expected,
    )

    assert (
        service._social_post_info(expected.webpage_url, "twitter", 1)
        == expected
    )
    assert service._cookie_file("twitter", 1) == (
        tmp_path / "users" / "1" / "twitter-cookies.txt"
    )


@pytest.mark.parametrize("platform", ["twitter", "reddit"])
def test_service_updates_social_cookies_without_restart(
    tmp_path, platform
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    content = (
        b"# Netscape HTTP Cookie File\n"
        b".example.test\tTRUE\t/\tTRUE\t0\tsession\tsecret\n"
    )

    path = service.update_cookies(platform, content)

    assert path == tmp_path / "users" / "1" / f"{platform}-cookies.txt"
    assert service._cookie_file(platform, 1) == path
    assert path.read_bytes() == content


def test_processed_media_keys_namespace_new_sources_and_preserve_tiktok(
    tmp_path,
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    tiktok = Video(
        "same-id",
        "creator",
        "",
        "https://www.tiktok.com/@creator/video/same-id",
        0,
    )
    twitter = Video(
        "same-id",
        "creator",
        "",
        "https://x.com/creator/status/1900000000000000001",
        0,
        platform="twitter",
    )
    reddit = Video(
        "same-id",
        "creator",
        "",
        "https://redd.it/same-id",
        0,
        platform="reddit",
    )

    for video in (tiktok, twitter, reddit):
        service.mark_processed(video)

    assert processed_media_key(tiktok) == "same-id"
    assert processed_media_key(twitter) == "twitter:same-id"
    assert processed_media_key(reddit) == "reddit:same-id"
    assert service.storage.has("same-id")
    assert service.storage.has("twitter:same-id")
    assert service.storage.has("reddit:same-id")


def test_publish_text_post_uses_send_message_and_4096_limit(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, data, timeout):
        captured.update(url=url, data=dict(data), timeout=timeout)
        return Response()

    monkeypatch.setattr("app.service.requests.post", fake_post)
    post = Video(
        "text1",
        "writer",
        "x" * 5000,
        "https://www.reddit.com/r/test/comments/text1/post/",
        0,
        platform="reddit",
        author_url="https://www.reddit.com/user/writer/",
        media_type="text",
    )

    service.publish(post, ())

    assert captured["url"].endswith("/sendMessage")
    assert captured["data"]["chat_id"] == "@main"
    assert captured["data"]["parse_mode"] == "HTML"
    assert len(captured["data"]["text"]) <= 4096
    assert "blockquote" in captured["data"]["text"]


def test_publish_text_custom_html_can_exceed_media_caption_limit(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(
        "app.service.requests.post",
        lambda url, data, timeout: (
            captured.update(url=url, data=dict(data)) or Response()
        ),
    )
    post = Video(
        "text2",
        "writer",
        "Fallback",
        "https://x.com/writer/status/1900000000000000002",
        0,
        platform="twitter",
        author_url="https://x.com/writer",
        media_type="text",
    )
    custom = f"<b>{'y' * 1500}</b><script>safe text</script>"

    service.publish(post, (), caption_html=custom)

    assert captured["url"].endswith("/sendMessage")
    assert len(captured["data"]["text"]) > 1024
    assert len(captured["data"]["text"]) <= 4096
    assert "<script>" not in captured["data"]["text"]
    assert "safe text" in captured["data"]["text"]


def test_publish_multiple_videos_uses_media_group_with_every_file(
    tmp_path, monkeypatch
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.webm"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, data, files, timeout):
        captured.update(
            url=url,
            data=dict(data),
            files=tuple(files),
            timeout=timeout,
        )
        return Response()

    monkeypatch.setattr("app.service.requests.post", fake_post)
    post = Video(
        "video1",
        "creator",
        "Two clips",
        "https://x.com/creator/status/1900000000000000001",
        0,
        platform="twitter",
        media_type="video",
    )

    service._publish_media_to_telegram(
        post,
        (first, second),
        "token",
        "@main",
        "Caption",
    )

    assert captured["url"].endswith("/sendMediaGroup")
    assert captured["files"] == ("video0", "video1")
    media = json.loads(captured["data"]["media"])
    assert [item["type"] for item in media] == ["video", "video"]
    assert all(item["supports_streaming"] for item in media)
    assert media[0]["caption"] == "Caption"


@pytest.mark.parametrize(
    ("command", "url", "platform"),
    [
        (
            "/x",
            "https://x.com/writer/status/1900000000000000002",
            "twitter",
        ),
        (
            "/twitter",
            "https://twitter.com/writer/status/1900000000000000002",
            "twitter",
        ),
        (
            "/reddit",
            "https://www.reddit.com/r/test/comments/text1/post/",
            "reddit",
        ),
    ],
)
def test_telegram_social_commands_publish_in_registered_chat(
    tmp_path, monkeypatch, command, url, platform
) -> None:
    service = TikTokToTelegram(make_service_config(tmp_path))
    service.storage.add_telegram_destination(
        "Private chat", "123", "command-token", destination_type="private"
    )
    video = Video(
        "post-id",
        "writer",
        "Text post",
        url,
        0,
        platform=platform,
        media_type="text",
    )
    published = {}

    class Response:
        def json(self):
            return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(
        "app.service.requests.post",
        lambda *args, **kwargs: Response(),
    )

    def fake_prepare(prepared_url, user_id):
        assert prepared_url == url
        assert user_id == 1
        return video, ()

    def fake_publish(
        video_arg,
        paths,
        bot_token,
        chat_id,
        caption,
        **kwargs,
    ):
        published.update(
            video=video_arg,
            paths=paths,
            bot_token=bot_token,
            chat_id=chat_id,
            caption=caption,
            kwargs=kwargs,
        )

    monkeypatch.setattr(service, "prepare_url", fake_prepare)
    monkeypatch.setattr(service, "_publish_media_to_telegram", fake_publish)

    service.process_telegram_update(
        {
            "message": {
                "text": f"{command} {url}",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 42},
            }
        },
        "command-token",
        1,
    )

    assert published["video"] == video
    assert published["paths"] == ()
    assert published["bot_token"] == "command-token"
    assert published["chat_id"] == "123"
    assert "Text post" in published["caption"]
