from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Config
from app.service import SpotifyTrack, TikTokToTelegram, Video


BOT_TOKEN = "123456:command-token"
CHAT_ID = -100123
TIKTOK_URL = "https://www.tiktok.com/@creator/video/1234567890"
INSTAGRAM_URL = "https://www.instagram.com/p/ABC_123/"
TWITTER_URL = "https://x.com/writer/status/1900000000000000002"
REDDIT_URL = "https://www.reddit.com/r/test/comments/text1/post/"
SPOTIFY_URL = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
YOUTUBE_URL = "https://youtu.be/abc123DEF45"


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def update_user_permissions(service: TikTokToTelegram, **changes: bool) -> None:
    user = service.user(1)
    values = {
        "username": user.username,
        "is_admin": user.is_admin,
        "is_disabled": user.is_disabled,
        **{
            f"allow_{platform}": user.allows(platform)
            for platform in (
                "tiktok", "instagram", "twitter", "reddit", "spotify", "youtube"
            )
        },
        **changes,
    }
    service.storage.update_user(1, **values)


@pytest.fixture
def bot(tmp_path: Path, monkeypatch):
    service = TikTokToTelegram(
        Config(
            telegram_bot_token=BOT_TOKEN,
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
    )
    service.storage.add_telegram_destination(
        "Post chat", str(CHAT_ID), BOT_TOKEN, destination_type="group"
    )
    observed = {"prepared": [], "published": [], "status": [], "finished": []}

    def prepare(url, user_id=1):
        observed["prepared"].append((url, user_id))
        platform = next(
            platform
            for platform, value in (
                ("tiktok", TIKTOK_URL),
                ("instagram", INSTAGRAM_URL),
                ("twitter", TWITTER_URL),
                ("reddit", REDDIT_URL),
            )
            if url == value
        )
        path = tmp_path / f"post-{len(observed['prepared'])}.mp4"
        path.write_bytes(b"video")
        return (
            Video(
                "post-id", "creator", "Post body", url, 0, platform=platform
            ),
            (path,),
        )

    def publish(video, paths, bot_token, chat_id, caption, **kwargs):
        assert all(path.is_file() for path in paths)
        observed["published"].append(
            {
                "video": video,
                "paths": paths,
                "token": bot_token,
                "chat": chat_id,
                "caption": caption,
                **kwargs,
            }
        )

    def send_status(*args, **kwargs):
        observed["status"].append((args, kwargs))
        return 900 + len(observed["status"])

    monkeypatch.setattr(service, "prepare_url", prepare)
    monkeypatch.setattr(service, "_publish_media_to_telegram", publish)
    monkeypatch.setattr(service, "_telegram_send_text", send_status)
    monkeypatch.setattr(
        service,
        "_telegram_finish_status",
        lambda *args, **kwargs: observed["finished"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        "app.service.requests.post",
        lambda *args, **kwargs: pytest.fail("unexpected Telegram request"),
    )
    yield service, observed
    service.storage.connection.close()


def incoming(text: str, **fields) -> dict:
    return {
        "message": {
            "message_id": 12,
            "text": text,
            "chat": {"id": CHAT_ID, "type": "supergroup"},
            "from": {"id": 42, "is_bot": False},
            "message_thread_id": 17,
            **fields,
        }
    }


@pytest.mark.parametrize(
    "platform,url",
    [
        ("tiktok", TIKTOK_URL),
        ("instagram", INSTAGRAM_URL),
        ("twitter", TWITTER_URL),
        ("reddit", REDDIT_URL),
        ("spotify", SPOTIFY_URL),
    ],
)
def test_message_link_parser_detects_supported_post_in_text(platform, url) -> None:
    assert TikTokToTelegram._telegram_message_links(
        {"text": f"Смотрите этот пост: ({url})."}
    ) == ((platform, url),)


def test_message_link_parser_preserves_order_and_deduplicates_canonical_urls() -> None:
    text = (
        f"{INSTAGRAM_URL}\n{SPOTIFY_URL}?si=first\n{TWITTER_URL}\n"
        f"{SPOTIFY_URL}?si=second\n{INSTAGRAM_URL}"
    )
    assert TikTokToTelegram._telegram_message_links({"text": text}) == (
        ("instagram", INSTAGRAM_URL),
        ("spotify", SPOTIFY_URL),
        ("twitter", TWITTER_URL),
    )


@pytest.mark.parametrize("post_type", ["p", "reel"])
def test_message_link_parser_accepts_instagram_post_with_author_path(post_type) -> None:
    url = f"https://www.instagram.com/creator/{post_type}/ABC_123/"
    assert TikTokToTelegram._telegram_message_links({"text": url}) == (
        ("instagram", url),
    )


def test_message_link_parser_reads_caption_hidden_links_and_utf16_url_entities() -> None:
    prefix = "😀 Пост "
    label = "ссылка"
    displayed_url = TWITTER_URL.removeprefix("https://")
    caption = f"{prefix}{label} и {displayed_url}"
    assert TikTokToTelegram._telegram_message_links(
        {
            "caption": caption,
            "caption_entities": [
                {
                    "type": "text_link",
                    "offset": utf16_length(prefix),
                    "length": utf16_length(label),
                    "url": INSTAGRAM_URL,
                },
                {
                    "type": "url",
                    "offset": utf16_length(f"{prefix}{label} и "),
                    "length": utf16_length(displayed_url),
                },
            ],
        }
    ) == (("instagram", INSTAGRAM_URL), ("twitter", TWITTER_URL))


def test_message_link_parser_deduplicates_url_present_in_text_and_entities() -> None:
    assert TikTokToTelegram._telegram_message_links(
        {
            "text": INSTAGRAM_URL,
            "entities": [
                {"type": "url", "offset": 0, "length": len(INSTAGRAM_URL)}
            ],
        }
    ) == (("instagram", INSTAGRAM_URL),)


def test_message_link_parser_uses_hidden_destination_when_label_looks_like_url() -> None:
    assert TikTokToTelegram._telegram_message_links(
        {
            "text": TWITTER_URL,
            "entities": [
                {
                    "type": "text_link",
                    "offset": 0,
                    "length": utf16_length(TWITTER_URL),
                    "url": INSTAGRAM_URL,
                }
            ],
        }
    ) == (("instagram", INSTAGRAM_URL),)


def test_message_link_parser_ignores_profiles_collections_and_unrelated_links() -> None:
    assert TikTokToTelegram._telegram_message_links(
        {
            "text": " ".join(
                (
                    "https://example.com/post",
                    "https://www.instagram.com/creator/",
                    "https://www.tiktok.com/@creator",
                    "https://x.com/writer",
                    "https://www.reddit.com/r/test/",
                    "https://www.youtube.com/playlist?list=PL123",
                    "https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC",
                    "https://x.com.evil.example/writer/status/123",
                )
            )
        }
    ) == ()


@pytest.mark.parametrize(
    "platform,url",
    [
        ("tiktok", TIKTOK_URL),
        ("instagram", INSTAGRAM_URL),
        ("twitter", TWITTER_URL),
        ("reddit", REDDIT_URL),
    ],
)
def test_message_without_command_publishes_post_in_same_chat_and_topic(
    bot, platform, url
) -> None:
    service, observed = bot
    service.process_telegram_update(incoming(f"Нашёл пост {url}"), BOT_TOKEN, 1)

    assert observed["prepared"] == [(url, 1)]
    assert len(observed["published"]) == 1
    published = observed["published"][0]
    assert published["video"].platform == platform
    assert published["token"] == BOT_TOKEN
    assert published["chat"] == str(CHAT_ID)
    assert published["message_thread_id"] == 17
    assert "Post body" in published["caption"]
    assert all(not path.exists() for path in published["paths"])
    assert observed["status"]
    assert observed["finished"]


def test_caption_without_command_publishes_hidden_link_in_channel(bot) -> None:
    service, observed = bot
    update = incoming(
        "",
        caption="😀 Ссылка",
        caption_entities=[
            {
                "type": "text_link",
                "offset": 3,
                "length": 6,
                "url": INSTAGRAM_URL,
            }
        ],
    )
    message = update.pop("message")
    message.pop("text")
    message.pop("from")
    message["chat"]["type"] = "channel"
    service.process_telegram_update({"channel_post": message}, BOT_TOKEN, 1)

    assert observed["prepared"] == [(INSTAGRAM_URL, 1)]
    assert observed["published"][0]["message_thread_id"] == 17


def test_message_publishes_multiple_distinct_links_once_in_order(bot) -> None:
    service, observed = bot
    service.process_telegram_update(
        incoming(f"{INSTAGRAM_URL}\n{TWITTER_URL}\n{INSTAGRAM_URL}"), BOT_TOKEN, 1
    )
    assert observed["prepared"] == [(INSTAGRAM_URL, 1), (TWITTER_URL, 1)]
    assert [item["video"].platform for item in observed["published"]] == [
        "instagram", "twitter"
    ]


@pytest.mark.parametrize(
    "text", ["Отличный пост", f"/help {INSTAGRAM_URL}", "https://example.com/post"]
)
def test_unrelated_message_and_unknown_command_do_not_rescan_reply(bot, text) -> None:
    service, observed = bot
    service.process_telegram_update(
        incoming(text, reply_to_message={"text": INSTAGRAM_URL}), BOT_TOKEN, 1
    )
    assert all(not values for values in observed.values())


@pytest.mark.parametrize("restriction", ["unregistered", "wrong_token", "disabled_service", "disabled_user", "bot_sender"])
def test_automatic_message_restrictions_have_no_side_effects(bot, restriction) -> None:
    service, observed = bot
    update = incoming(INSTAGRAM_URL)
    token = BOT_TOKEN
    if restriction == "unregistered":
        update["message"]["chat"]["id"] = -100999
    elif restriction == "wrong_token":
        token = "654321:other-token"
    elif restriction == "disabled_service":
        update_user_permissions(service, allow_instagram=False)
    elif restriction == "disabled_user":
        update_user_permissions(service, is_disabled=True)
    elif restriction == "bot_sender":
        update["message"]["from"] = {"id": 123456, "is_bot": True}

    service.process_telegram_update(update, token, 1)
    assert all(not values for values in observed.values())


def test_anonymous_chat_admin_is_allowed_despite_bot_shaped_sender(bot) -> None:
    service, observed = bot
    service.process_telegram_update(
        incoming(
            INSTAGRAM_URL,
            **{
                "from": {"id": 1087968824, "is_bot": True},
                "sender_chat": {"id": CHAT_ID, "type": "supergroup"},
            },
        ),
        BOT_TOKEN,
        1,
    )
    assert observed["prepared"] == [(INSTAGRAM_URL, 1)]


@pytest.mark.parametrize("platform", ["tiktok", "youtube"])
def test_polling_includes_tiktok_only_users_and_excludes_youtube_only_users(bot, platform) -> None:
    service, _ = bot
    update_user_permissions(
        service,
        **{
            f"allow_{name}": name == platform
            for name in (
                "tiktok", "instagram", "twitter", "reddit", "spotify", "youtube"
            )
        },
    )
    assert service._telegram_bot_owners() == ({BOT_TOKEN: 1} if platform == "tiktok" else {})


def test_spotify_link_without_command_downloads_and_publishes_track(bot, tmp_path, monkeypatch) -> None:
    service, observed = bot
    track = SpotifyTrack("4uLU6hMCjMI75M1A2tKUQC", "Track", SPOTIFY_URL, "", "Artist")
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"mp3")
    metadata = []
    published = []

    def info(url, user_id=1):
        metadata.append((url, user_id))
        return track

    monkeypatch.setattr(service, "get_spotify_info", info)
    monkeypatch.setattr(service, "download_spotify", lambda *args, **kwargs: audio)
    monkeypatch.setattr(
        service,
        "_publish_spotify_to_telegram",
        lambda *args, **kwargs: published.append((args, kwargs)),
    )
    service.process_telegram_update(incoming(f"Музыка {SPOTIFY_URL}?si=share"), BOT_TOKEN, 1)

    assert metadata == [(SPOTIFY_URL, 1)]
    assert published == [
        ((track, audio, BOT_TOKEN, str(CHAT_ID)), {"caption_html": None, "message_thread_id": 17})
    ]
    assert observed["status"]
    assert observed["finished"]


@pytest.mark.parametrize(
    "url",
    [
        YOUTUBE_URL,
        "https://www.youtube.com/watch?v=abc123DEF45",
        "https://www.youtube.com/shorts/abc123DEF45",
        "https://www.youtube.com/live/abc123DEF45",
    ],
)
@pytest.mark.parametrize("format", ["text", "caption", "text_link"])
def test_youtube_links_are_ignored_without_metadata_or_telegram_requests(
    bot, monkeypatch, url, format
) -> None:
    service, observed = bot
    monkeypatch.setattr(
        service,
        "get_youtube_info",
        lambda *args, **kwargs: pytest.fail("YouTube metadata must not be fetched"),
    )
    update = incoming(url)
    message = update["message"]
    if format == "caption":
        message.pop("text")
        message["caption"] = url
    elif format == "text_link":
        message["text"] = "Смотреть видео"
        message["entities"] = [
            {
                "type": "text_link",
                "offset": 0,
                "length": utf16_length(message["text"]),
                "url": url,
            }
        ]

    assert service._telegram_message_links(message) == ()
    service.process_telegram_update(update, BOT_TOKEN, 1)
    assert all(not values for values in observed.values())


def test_youtube_link_does_not_prevent_instagram_download_in_mixed_message(bot) -> None:
    service, observed = bot
    service.process_telegram_update(
        incoming(f"{YOUTUBE_URL}\n{INSTAGRAM_URL}"), BOT_TOKEN, 1
    )
    assert observed["prepared"] == [(INSTAGRAM_URL, 1)]
    assert [item["video"].platform for item in observed["published"]] == ["instagram"]


def test_human_forward_of_other_bot_post_downloads_hidden_original_link(bot) -> None:
    service, observed = bot
    caption = "Скачано через SaveAsBot\nОригинал"
    update = incoming(
        "",
        caption=caption,
        caption_entities=[
            {
                "type": "text_link",
                "offset": utf16_length("Скачано через SaveAsBot\n"),
                "length": utf16_length("Оригинал"),
                "url": INSTAGRAM_URL,
            }
        ],
        forward_origin={
            "type": "user",
            "date": 1791248400,
            "sender_user": {
                "id": 654321,
                "is_bot": True,
                "username": "SaveAsBot",
            },
        },
    )
    update["message"].pop("text")

    assert update["message"]["from"]["is_bot"] is False
    assert service._telegram_message_links(update["message"]) == (
        ("instagram", INSTAGRAM_URL),
    )
    service.process_telegram_update(update, BOT_TOKEN, 1)
    assert observed["prepared"] == [(INSTAGRAM_URL, 1)]
    assert [item["video"].platform for item in observed["published"]] == ["instagram"]


def test_failed_first_link_does_not_block_next_post_and_cleans_files(bot, monkeypatch) -> None:
    service, observed = bot
    publish = service._publish_media_to_telegram

    def fail_first(video, paths, *args, **kwargs):
        if video.platform == "instagram":
            assert all(path.exists() for path in paths)
            raise RuntimeError("upload failed")
        return publish(video, paths, *args, **kwargs)

    monkeypatch.setattr(service, "_publish_media_to_telegram", fail_first)
    service.process_telegram_update(incoming(f"{INSTAGRAM_URL}\n{TWITTER_URL}"), BOT_TOKEN, 1)

    assert observed["prepared"] == [(INSTAGRAM_URL, 1), (TWITTER_URL, 1)]
    assert [item["video"].platform for item in observed["published"]] == ["twitter"]
    assert not tuple(service.config.data_dir.glob("post-*.mp4"))
    assert len(observed["finished"]) == 2


@pytest.mark.parametrize("discussion_forward", [False, True])
def test_generated_channel_post_is_ignored_after_restart(
    bot, monkeypatch, discussion_forward
) -> None:
    service, _ = bot
    service.storage.add_telegram_destination(
        "Discussion", "-100124", BOT_TOKEN, destination_type="group"
    )
    post = Video(
        "post-id", "creator", "Post body", INSTAGRAM_URL, 0,
        platform="instagram", media_type="text",
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "ok": True,
                "result": {
                    "message_id": 101,
                    "chat": {"id": CHAT_ID, "type": "channel"},
                },
            }

    monkeypatch.setattr(
        "app.service.requests.post", lambda *args, **kwargs: Response()
    )
    TikTokToTelegram._publish_media_to_telegram(
        service, post, (), BOT_TOKEN, str(CHAT_ID), INSTAGRAM_URL
    )

    restarted = TikTokToTelegram(service.config)
    monkeypatch.setattr(
        restarted,
        "prepare_url",
        lambda *args, **kwargs: pytest.fail("our published post must not be downloaded"),
    )
    monkeypatch.setattr(
        "app.service.requests.post",
        lambda *args, **kwargs: pytest.fail("our published post must be ignored"),
    )
    message = {
        "message_id": 101,
        "text": INSTAGRAM_URL,
        "chat": {"id": CHAT_ID, "type": "channel"},
    }
    if discussion_forward:
        message.update(
            message_id=201,
            chat={"id": -100124, "type": "supergroup"},
            is_automatic_forward=True,
            forward_origin={
                "type": "channel",
                "chat": {"id": CHAT_ID, "type": "channel"},
                "message_id": 101,
            },
        )
    try:
        restarted.process_telegram_update(
            {"message" if discussion_forward else "channel_post": message}, BOT_TOKEN, 1
        )
    finally:
        restarted.storage.connection.close()


def test_generated_album_remembers_each_message_id(bot, tmp_path, monkeypatch) -> None:
    service, _ = bot
    files = (tmp_path / "one.jpg", tmp_path / "two.jpg")
    for path in files:
        path.write_bytes(b"image")
    post = Video(
        "post-id", "creator", "Post body", INSTAGRAM_URL, 0,
        platform="instagram", media_type="image",
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "ok": True,
                "result": [
                    {"message_id": message_id, "chat": {"id": CHAT_ID}}
                    for message_id in (102, 103)
                ],
            }

    monkeypatch.setattr(
        "app.service.requests.post", lambda *args, **kwargs: Response()
    )
    TikTokToTelegram._publish_media_to_telegram(
        service, post, files, BOT_TOKEN, str(CHAT_ID), INSTAGRAM_URL
    )
    for message_id in (102, 103):
        assert service._telegram_message_was_sent(
            {"chat": {"id": CHAT_ID}, "message_id": message_id}, BOT_TOKEN
        )
    assert not service._telegram_message_was_sent(
        {"chat": {"id": -100999}, "message_id": 102}, BOT_TOKEN
    )
    assert not service._telegram_message_was_sent(
        {"chat": {"id": CHAT_ID}, "message_id": 102}, "other-bot:token"
    )
