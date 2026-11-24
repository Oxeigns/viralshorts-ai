from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from passlib.context import CryptContext

from app.config import get_settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().app_secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def new_csrf() -> str:
    return secrets.token_urlsafe(32)


def sign_session(payload: dict, hours: int = 24) -> str:
    body = {
        **payload,
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp()),
        "nonce": secrets.token_hex(8),
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    key = get_settings().app_secret_key.encode()
    sig = hmac.new(key, raw, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(raw).decode() + "." + sig


def read_session(token: str) -> dict | None:
    try:
        raw_b64, sig = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64.encode())
        key = get_settings().app_secret_key.encode()
        expected = hmac.new(key, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        body = json.loads(raw)
        if int(body.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return body
    except Exception:
        return None


def random_state() -> str:
    return secrets.token_urlsafe(24)


def generate_secret_key() -> str:
    return os.urandom(32).hex()
