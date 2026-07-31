from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
from librespot.core import ApResolver, Session
from librespot.metadata import TrackId
from librespot.proto import Authentication_pb2 as Authentication


def create_session(access_token: str) -> Session:
    login_credentials = Authentication.LoginCredentials(
        username=None,
        typ=Authentication.AuthenticationType.AUTHENTICATION_SPOTIFY_TOKEN,
        auth_data=access_token.encode(),
    )
    builder = Session.Builder()
    builder.login_credentials = login_credentials
    builder.conf = (
        Session.Configuration.Builder()
        .set_store_credentials(False)
        .set_cache_enabled(False)
        .build()
    )

    access_points = ApResolver.request("accesspoint").get("accesspoint", [])
    errors: list[str] = []
    for access_point in access_points:
        session = None
        try:
            host, port = access_point.rsplit(":", 1)
            with socket.create_connection((host, int(port)), timeout=5):
                pass
            inner = Session.Inner(
                builder.device_type,
                builder.device_name,
                builder.preferred_locale,
                builder.conf,
                builder.device_id,
            )
            session = Session(inner, access_point)
            session.connect()
            session.authenticate(builder.login_credentials)
            return session
        except Exception as error:
            errors.append(f"{access_point}: {type(error).__name__}")
            if session:
                try:
                    session.close()
                except Exception:
                    pass
    raise ConnectionError(
        "No reachable Spotify access point: " + ", ".join(errors[:5])
    )


def download_track(token_path: Path, track_id: str, output_path: Path) -> None:
    session_payload = json.loads(token_path.read_text(encoding="utf-8"))
    access_token = str(session_payload.get("access_token") or "")
    if not access_token:
        raise ValueError("Spotify access token is missing")

    session = create_session(access_token)
    try:
        stream = session.content_feeder().load(
            TrackId.from_base62(track_id),
            VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH),
            False,
            None,
        )
        input_stream = stream.input_stream
        reader = input_stream.stream()
        total_size = int(input_stream.size)
        written = 0
        with output_path.open("wb") as destination:
            while written < total_size:
                chunk = reader.read(min(64 * 1024, total_size - written))
                if not chunk:
                    break
                destination.write(chunk)
                written += len(chunk)
        if written < max(1, total_size - 1024):
            raise IOError(
                f"Spotify stream ended early: received {written} of {total_size} bytes"
            )
        with output_path.open("rb") as downloaded:
            if downloaded.read(4) != b"OggS":
                raise IOError("Spotify player returned an invalid OGG stream")
    finally:
        session.close()


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "Usage: spotify_player TOKEN_PATH TRACK_ID OUTPUT_PATH"
        )
    download_track(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))


if __name__ == "__main__":
    main()
