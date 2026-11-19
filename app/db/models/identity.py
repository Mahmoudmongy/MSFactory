"""Identity and tenancy: users, companies, memberships, sessions."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """A person who can authenticate. A user may belong to many companies."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships = relationship("Membership", back_populates="user", cascade="all, delete-orphan")


class Company(Base, TimestampMixin):
    """A tenant. All financial data is scoped to exactly one company."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(200), default="")
    tax_number: Mapped[str] = mapped_column(String(60), default="")
    commercial_register: Mapped[str] = mapped_column(String(60), default="")
    country: Mapped[str] = mapped_column(String(2), default="EG")
    base_currency: Mapped[str] = mapped_column(String(3), default="EGP")
    timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships = relationship("Membership", back_populates="company", cascade="all, delete-orphan")
    settings = relationship("CompanySetting", back_populates="company", cascade="all, delete-orphan")


class Membership(Base):
    """Links a user to a company with a role. This is the tenant boundary."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id", name="uq_memberships_user_company"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user = relationship("User", back_populates="memberships")
    company = relationship("Company", back_populates="memberships")


class RefreshToken(Base):
    """Persisted refresh tokens so they can be revoked (logout, theft)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanySetting(Base):
    """Per-company key/value configuration (tax rates, invoice prefixes...)."""

    __tablename__ = "company_settings"
    __table_args__ = (UniqueConstraint("company_id", "key", name="uq_company_settings_company_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    # Text, not String(500): a company logo is stored here as a data URL.
    value: Mapped[str] = mapped_column(Text, default="")

    company = relationship("Company", back_populates="settings")
