from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from votify.api.api import SpotifyApi
from votify.api.enums import SessionType


async def fetch_session(cookies_path: Path, output_path: Path) -> None:
    api = await SpotifyApi.create_from_netscape_cookies(
        str(cookies_path),
        session_type=SessionType.WEB,
    )
    try:
        output_path.write_text(
            json.dumps(
                {
                    "access_token": api._access_token,
                    "premium": api.premium_session,
                }
            ),
            encoding="utf-8",
        )
    finally:
        await api.client.aclose()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: spotify_auth COOKIES_PATH OUTPUT_PATH")
    asyncio.run(fetch_session(Path(sys.argv[1]), Path(sys.argv[2])))


if __name__ == "__main__":
    main()
