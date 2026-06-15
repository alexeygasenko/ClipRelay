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
