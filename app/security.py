from __future__ import annotations

import hmac
from urllib.parse import unquote, urlsplit


def safe_redirect_target(candidate: str | None, host_url: str | None) -> str | None:
    """Return a safe root-relative redirect target, or ``None`` when unsafe."""
    if not isinstance(candidate, str) or not isinstance(host_url, str):
        return None
    if not candidate or candidate != candidate.strip():
        return None

    try:
        host = urlsplit(host_url)
    except ValueError:
        return None
    if host.scheme not in {"http", "https"} or not host.netloc:
        return None

    variants = [candidate]
    for _ in range(4):
        decoded = unquote(variants[-1])
        if decoded == variants[-1]:
            break
        variants.append(decoded)

    for value in variants:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return None
        if "\\" in value:
            return None
        if not value.startswith("/") or value.startswith("//"):
            return None

        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc:
            return None

    return candidate


def verify_bootstrap_credentials(
    configured_username: str | None,
    configured_password: str | None,
    supplied_username: str | None,
    supplied_password: str | None,
) -> bool:
    """Compare configured bootstrap credentials in constant time."""
    if (
        not isinstance(configured_username, str)
        or not isinstance(configured_password, str)
        or not isinstance(supplied_username, str)
        or not isinstance(supplied_password, str)
    ):
        return False
    if not configured_username.strip() or not configured_password.strip():
        return False

    username_matches = hmac.compare_digest(
        configured_username,
        supplied_username,
    )
    password_matches = hmac.compare_digest(
        configured_password,
        supplied_password,
    )
    return username_matches & password_matches
