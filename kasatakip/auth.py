"""Şifre özetleme (PBKDF2-SHA256) ve kurtarma anahtarı."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from .errors import ServiceError

ITERATIONS = 240_000
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # karışan harfler (I, O, 0, 1) yok


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                   bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(calc, bytes.fromhex(digest_hex))
    except (ValueError, AttributeError):
        return False


def check_new_password(password: str) -> None:
    if password is None or len(password) < 4:
        raise ServiceError("Şifre en az 4 karakter olmalı.")
    if len(password) > 128:
        raise ServiceError("Şifre çok uzun.")
    if password != password.strip():
        raise ServiceError("Şifre boşlukla başlayamaz veya bitemez.")


def new_recovery_key() -> str:
    return "-".join("".join(secrets.choice(_KEY_ALPHABET) for _ in range(4)) for _ in range(4))


def normalize_key(key: str) -> str:
    return "".join(ch for ch in (key or "").upper() if ch.isalnum())
