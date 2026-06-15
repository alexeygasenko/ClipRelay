from pathlib import Path

from app.config import Config


def test_loads_yaml(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
telegram:
  bot_token: secret-token
  chat_id: "@channel"
  channels:
    - name: Main
      chat_id: "@channel"
    - name: Second
      chat_id: "@second"
tiktok:
  channels: ["@one", "@two"]
  poll_interval_seconds: 120
web:
  port: 9000
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_file))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    config = Config.from_sources()

    assert config.telegram_bot_token == "secret-token"
    assert config.telegram_chat_id == "@channel"
    assert [channel.chat_id for channel in config.telegram_channels] == [
        "@channel",
        "@second",
    ]
    assert config.tiktok_channels == ("@one", "@two")
    assert config.poll_interval_seconds == 120
    assert config.web_port == 9000
    assert config.data_dir == tmp_path / "data"


def test_relative_cookie_paths_are_resolved_from_config(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
telegram:
  bot_token: token
  chat_id: "@channel"
tiktok:
  cookies_file: tiktok-cookies.txt
instagram:
  cookies_file: instagram-cookies.txt
youtube:
  cookies_file: youtube-cookies.txt
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_file))

    config = Config.from_sources()

    assert config.cookies_file == tmp_path / "tiktok-cookies.txt"
    assert config.instagram_cookies_file == tmp_path / "instagram-cookies.txt"
    assert config.youtube_cookies_file == tmp_path / "youtube-cookies.txt"


def test_environment_overrides_yaml(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "telegram:\n  bot_token: yaml-token\n  chat_id: '@yaml'\nweb:\n  port: 8080\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_file))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@env")
    monkeypatch.setenv("WEB_PORT", "9090")

    config = Config.from_sources()

    assert config.telegram_bot_token == "env-token"
    assert config.telegram_chat_id == "@env"
    assert config.web_port == 9090


def test_rejects_unknown_telegram_channel(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "telegram:\n  bot_token: token\n  chat_id: '@main'\n  channels: ['@main', '@other']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_file))
    config = Config.from_sources()

    assert config.validate_telegram_chat_id("@other") == "@other"
    try:
        config.validate_telegram_chat_id("@unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown Telegram channel must be rejected")
