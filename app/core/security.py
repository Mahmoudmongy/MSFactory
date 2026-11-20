"""Password hashing (Argon2id) and JWT token issuing/verification."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .config import settings

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
)

ALGORITHM = "HS256"

# Long enough to open the tab and print, short enough that a leaked link dies
# before it is useful. A print job is a one-off action, not a session.
DOCUMENT_TOKEN_MINUTES = 15


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def create_access_token(user_id: int, company_id: int | None = None, role: str = "") -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "cid": company_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(user_id: int, jti: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def create_document_token(user_id: int, company_id: int, document: str, document_id: int) -> str:
    """A short-lived token that opens exactly one printable document.

    A print URL is opened in a new tab or handed to another device, and putting
    the access token in a URL leaks it into history, logs and shared links.
    This token carries only the document it names, expires in minutes, and is
    rejected by every other endpoint because its ``type`` is not ``access``.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "document",
        "cid": company_id,
        "doc": document,
        "did": document_id,
        "iat": now,
        "exp": now + timedelta(minutes=DOCUMENT_TOKEN_MINUTES),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_document_token(token: str, document: str, document_id: int) -> dict | None:
    """Verify a print token and that it names this exact document."""
    payload = decode_token(token)
    if not payload or payload.get("type") != "document":
        return None
    if payload.get("doc") != document or payload.get("did") != document_id:
        return None
    return payload
