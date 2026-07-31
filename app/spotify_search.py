from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from votify.api.api import SpotifyApi
from votify.api.enums import SessionType


SPOTIFY_SEARCH_HASH = (
    "903df2a65d8121e27d73a2be03c01e88ebe6021bb6d4eb82a389e35d87e51d27"
)


def _largest_image(sources: list[dict[str, Any]]) -> str:
    images = [
        source
        for source in sources
        if isinstance(source, dict) and str(source.get("url") or "").strip()
    ]
    if not images:
        return ""
    image = max(
        images,
        key=lambda source: int(source.get("width") or 0)
        * int(source.get("height") or 0),
    )
    return str(image["url"])


def track_from_pathfinder(payload: dict[str, Any]) -> dict[str, str] | None:
    items = (
        payload.get("data", {})
        .get("searchV2", {})
        .get("tracksV2", {})
        .get("items", [])
    )
    for wrapper in items:
        track = (wrapper.get("item") or {}).get("data") or {}
        track_id = str(track.get("id") or "").strip()
        title = str(track.get("name") or "").strip()
        if not track_id or not title:
            continue
        artists = [
            str((artist.get("profile") or {}).get("name") or "").strip()
            for artist in (track.get("artists") or {}).get("items", [])
            if isinstance(artist, dict)
        ]
        album = track.get("albumOfTrack") or {}
        thumbnail_url = _largest_image(
            (album.get("coverArt") or {}).get("sources", [])
        )
        return {
            "track_id": track_id,
            "title": title,
            "artist": ", ".join(name for name in artists if name),
            "thumbnail_url": thumbnail_url,
            "url": f"https://open.spotify.com/track/{track_id}",
        }
    return None


def track_from_web_api(payload: dict[str, Any]) -> dict[str, str] | None:
    for track in (payload.get("tracks") or {}).get("items", []):
        track_id = str(track.get("id") or "").strip()
        title = str(track.get("name") or "").strip()
        if not track_id or not title:
            continue
        artists = [
            str(artist.get("name") or "").strip()
            for artist in track.get("artists", [])
            if isinstance(artist, dict)
        ]
        thumbnail_url = _largest_image(
            (track.get("album") or {}).get("images", [])
        )
        return {
            "track_id": track_id,
            "title": title,
            "artist": ", ".join(name for name in artists if name),
            "thumbnail_url": thumbnail_url,
            "url": f"https://open.spotify.com/track/{track_id}",
        }
    return None


async def search_track(cookies_path: Path, query: str) -> dict[str, str]:
    api = await SpotifyApi.create_from_netscape_cookies(
        str(cookies_path),
        session_type=SessionType.WEB,
    )
    try:
        track = None
        try:
            payload = await api._pathfinder_request(
                "findTracks",
                SPOTIFY_SEARCH_HASH,
                {"query": query, "limit": 10, "offset": 0},
            )
            track = track_from_pathfinder(payload)
        except Exception:
            response = await api.client.get(
                "https://api.spotify.com/v1/search",
                params={"q": query, "type": "track", "limit": 10},
            )
            if response.status_code == 200:
                track = track_from_web_api(response.json())
        if not track:
            raise ValueError("Spotify не нашёл подходящих треков")
        return track
    finally:
        await api.client.aclose()


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "Usage: spotify_search COOKIES_PATH QUERY OUTPUT_PATH"
        )
    result = asyncio.run(search_track(Path(sys.argv[1]), sys.argv[2]))
    Path(sys.argv[3]).write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
