from types import SimpleNamespace

from app import main as main_module


def test_main_starts_background_workers_and_uvicorn(monkeypatch) -> None:
    config = SimpleNamespace(web_host="127.0.0.1", web_port=8080)
    service = SimpleNamespace(
        run_forever=lambda: None,
        run_telegram_commands_forever=lambda: None,
    )
    application = object()
    threads: list[dict[str, object]] = []
    uvicorn_call: dict[str, object] = {}

    class FakeThread:
        def __init__(self, *, target, daemon: bool, name: str) -> None:
            self.record = {
                "target": target,
                "daemon": daemon,
                "name": name,
                "started": False,
            }
            threads.append(self.record)

        def start(self) -> None:
            self.record["started"] = True

    monkeypatch.setattr(
        main_module.Config,
        "from_sources",
        classmethod(lambda cls: config),
    )
    monkeypatch.setattr(
        main_module,
        "TikTokToTelegram",
        lambda received: service if received is config else None,
    )
    monkeypatch.setattr(
        main_module,
        "create_app",
        lambda received_config, received_service: (
            application
            if received_config is config and received_service is service
            else None
        ),
    )
    monkeypatch.setattr(main_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda app, **kwargs: uvicorn_call.update(app=app, **kwargs),
    )

    main_module.main()

    assert threads == [
        {
            "target": service.run_forever,
            "daemon": True,
            "name": "monitor",
            "started": True,
        },
        {
            "target": service.run_telegram_commands_forever,
            "daemon": True,
            "name": "telegram-commands",
            "started": True,
        },
    ]
    assert uvicorn_call == {
        "app": application,
        "host": "127.0.0.1",
        "port": 8080,
        "server_header": False,
    }
