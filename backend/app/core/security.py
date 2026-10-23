"""Secure password hashing using PBKDF2 (standard library only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Number of PBKDF2-HMAC-SHA256 iterations. Tuned for ~100ms on a modern CPU.
PBKDF2_ITERATIONS = 240_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return a stored string of the form salt_b64$iterations$hash_b64."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{base64.b64encode(salt).decode('ascii')}${PBKDF2_ITERATIONS}${base64.b64encode(dk).decode('ascii')}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, it_str, hash_b64 = stored.split("$")
        iterations = int(it_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)
