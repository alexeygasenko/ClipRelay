import logging
import threading

import uvicorn

from app.config import Config
from app.service import TikTokToTelegram
from app.web import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_sources()
    service = TikTokToTelegram(config)
    threading.Thread(target=service.run_forever, daemon=True, name="monitor").start()
    threading.Thread(
        target=service.run_telegram_commands_forever,
        daemon=True,
        name="telegram-commands",
    ).start()
    uvicorn.run(
        create_app(config, service),
        host=config.web_host,
        port=config.web_port,
        server_header=False,
    )


if __name__ == "__main__":
    main()
