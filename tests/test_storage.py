import sqlite3

from app.storage import Storage


def test_repeated_config_migration_keeps_default_destination(tmp_path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")

    storage.add_telegram_destination(
        "Main", "@main", "token", is_default=True, replace=False
    )
    storage.add_telegram_destination(
        "Main", "@main", "token", is_default=True, replace=False
    )

    assert storage.telegram_destination().chat_id == "@main"
    assert storage.telegram_destination().is_default


def test_deleted_config_destination_does_not_return(tmp_path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.add_telegram_destination("Main", "@main", "token", is_default=True)
    storage.add_telegram_destination("Other", "@other", "token")

    storage.delete_telegram_destination("@other")
    storage.add_telegram_destination("Other", "@other", "token", replace=False)

    assert [item.chat_id for item in storage.telegram_destinations()] == ["@main"]


def test_destinations_can_be_ordered_and_include_chat_type(tmp_path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.add_telegram_destination("Channel", "@channel", "token", is_default=True)
    storage.add_telegram_destination(
        "Chat", "-123", "token", destination_type="supergroup"
    )

    storage.move_telegram_destination("-123", -1)

    destinations = storage.telegram_destinations()
    assert [item.chat_id for item in destinations] == ["-123", "@channel"]
    assert destinations[0].destination_type == "supergroup"


def test_monitored_tiktok_channels_and_interval_are_persisted(tmp_path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.add_monitored_tiktok_channel("author")
    storage.set_setting("poll_interval_seconds", "120")

    assert storage.monitored_tiktok_channels() == ("author",)
    assert storage.setting("poll_interval_seconds", "300") == "120"

    storage.delete_monitored_tiktok_channel("author")
    assert storage.monitored_tiktok_channels() == ()


def test_admin_user_is_created_and_settings_are_user_scoped(tmp_path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")

    admin = storage.get_user(1)
    assert admin is not None
    assert admin.username == "boyd"
    assert admin.is_admin
    assert admin.must_set_password

    user = storage.create_user("alice", "hash")
    storage.add_telegram_destination("Admin", "@admin", "token", user_id=admin.id)
    storage.add_telegram_destination("Alice", "@alice", "token", user_id=user.id)
    storage.add_monitored_tiktok_channel("admin_channel", user_id=admin.id)
    storage.add_monitored_tiktok_channel("alice_channel", user_id=user.id)

    assert [item.chat_id for item in storage.telegram_destinations(admin.id)] == ["@admin"]
    assert [item.chat_id for item in storage.telegram_destinations(user.id)] == ["@alice"]
    assert storage.monitored_tiktok_channels(admin.id) == ("admin_channel",)
    assert storage.monitored_tiktok_channels(user.id) == ("alice_channel",)


def test_user_permissions_include_twitter_and_reddit(tmp_path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    user = storage.create_user("alice", "hash")

    assert user.allow_twitter
    assert user.allow_reddit
    assert user.allows("twitter")
    assert user.allows("reddit")

    storage.update_user(
        user.id,
        username=user.username,
        is_admin=False,
        is_disabled=False,
        allow_tiktok=True,
        allow_instagram=True,
        allow_youtube=True,
        allow_spotify=True,
        allow_twitter=False,
        allow_reddit=False,
    )

    updated = storage.get_user(user.id)
    assert updated is not None
    assert not updated.allow_twitter
    assert not updated.allow_reddit


def test_user_schema_migration_adds_social_permissions_with_safe_defaults(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_disabled INTEGER NOT NULL DEFAULT 0,
            allow_tiktok INTEGER NOT NULL DEFAULT 1,
            allow_instagram INTEGER NOT NULL DEFAULT 1,
            allow_youtube INTEGER NOT NULL DEFAULT 1,
            allow_spotify INTEGER NOT NULL DEFAULT 1,
            must_set_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO users(
            id, username, password_hash, is_admin, must_set_password
        ) VALUES (1, 'existing-admin', 'hash', 1, 0)
        """
    )
    connection.commit()
    connection.close()

    storage = Storage(database)

    user = storage.get_user(1)
    assert user is not None
    assert user.username == "existing-admin"
    assert user.allow_twitter
    assert user.allow_reddit
