"""Password hashing and token helpers (stdlib only, no extra deps)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_ITERATIONS = 200_000
_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_b64, hash_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_SECRET.encode(), payload, hashlib.sha256).digest()).decode()


def create_token(user_id: int) -> str:
    body = json.dumps({"uid": user_id, "exp": int(time.time()) + TOKEN_TTL}).encode()
    return f"{base64.urlsafe_b64encode(body).decode()}.{_sign(body)}"


def decode_token(token: str) -> int | None:
    try:
        body_b64, signature = token.split(".")
        body = base64.urlsafe_b64decode(body_b64)
        if not hmac.compare_digest(_sign(body), signature):
            return None
        data = json.loads(body)
        if data["exp"] < time.time():
            return None
        return int(data["uid"])
    except (ValueError, KeyError, TypeError):
        return None
