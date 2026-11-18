"""Authentication service: login, refresh rotation, logout."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.errors import AuthenticationError
from ..core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from ..db.models import Membership, RefreshToken, User

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def register(self, *, email: str, password: str, full_name: str = "") -> User:
        email = email.strip().lower()
        if self.db.scalar(select(User).where(User.email == email)):
            from ..core.errors import ConflictError

            raise ConflictError("An account with this email already exists")
        user = User(email=email, full_name=full_name, password_hash=hash_password(password))
        self.db.add(user)
        self.db.flush()
        return user

    def authenticate(self, *, email: str, password: str) -> User:
        user = self.db.scalar(select(User).where(User.email == email.strip().lower()))
        # Always run a hash comparison so timing does not reveal whether the
        # email exists.
        if not user:
            hash_password(password)
            raise AuthenticationError("Invalid email or password")

        now = datetime.now(UTC)
        if user.locked_until and user.locked_until > now:
            raise AuthenticationError("Account temporarily locked after repeated failed logins")
        if not user.is_active:
            raise AuthenticationError("Account is disabled")

        if not verify_password(password, user.password_hash):
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_count = 0
            self.db.flush()
            raise AuthenticationError("Invalid email or password")

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        self.db.flush()
        return user

    def issue_tokens(self, user: User, company_id: int | None = None, role: str = "") -> dict:
        jti = secrets.token_urlsafe(32)
        refresh = create_refresh_token(user.id, jti)
        self.db.add(
            RefreshToken(
                user_id=user.id,
                jti=jti,
                expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
                created_at=datetime.now(UTC),
            )
        )
        self.db.flush()
        return {
            "access_token": create_access_token(user.id, company_id, role),
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": settings.access_token_minutes * 60,
        }

    def refresh(self, refresh_token: str) -> dict:
        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise AuthenticationError("Invalid refresh token")

        record = self.db.scalar(select(RefreshToken).where(RefreshToken.jti == payload["jti"]))
        if not record or record.revoked_at:
            # A reused/revoked token means possible theft: revoke the whole
            # family by refusing to continue.
            raise AuthenticationError("Refresh token is no longer valid")
        if record.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired")

        user = self.db.get(User, int(payload["sub"]))
        if not user or not user.is_active:
            raise AuthenticationError("Account is disabled")

        record.revoked_at = datetime.now(UTC)  # rotate on every use
        membership = self.db.scalar(
            select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
        )
        return self.issue_tokens(
            user,
            company_id=membership.company_id if membership else None,
            role=membership.role if membership else "",
        )

    def logout(self, refresh_token: str) -> None:
        payload = decode_token(refresh_token)
        if not payload:
            return
        record = self.db.scalar(select(RefreshToken).where(RefreshToken.jti == payload.get("jti")))
        if record and not record.revoked_at:
            record.revoked_at = datetime.now(UTC)
            self.db.flush()
