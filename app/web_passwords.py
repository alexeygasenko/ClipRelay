from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_password_hash(password: str) -> str:
    """Create a Werkzeug-compatible scrypt password hash without Werkzeug."""
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=32768,
        r=8,
        p=1,
        maxmem=132 * 32768 * 8,
        dklen=64,
    )
    return f"scrypt:32768:8:1${salt}${digest.hex()}"


def check_password_hash(encoded: str, password: str) -> bool:
    """Verify current scrypt hashes and legacy Werkzeug PBKDF2 hashes."""
    try:
        method, salt, expected = encoded.split("$", 2)
        if method.startswith("scrypt:"):
            _, n, r, p = method.split(":")
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt.encode("utf-8"),
                n=int(n),
                r=int(r),
                p=int(p),
                maxmem=132 * int(n) * int(r) * int(p),
                dklen=len(bytes.fromhex(expected)),
            ).hex()
        elif method.startswith("pbkdf2:"):
            _, algorithm, iterations = method.split(":")
            actual = hashlib.pbkdf2_hmac(
                algorithm,
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
        else:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)
