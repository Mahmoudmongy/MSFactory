"""Migration from another accounting system into these books.

Two tables, both per tenant:

``ImportMapping`` remembers that Manager.io's customer ``<uuid>`` became our
contact ``#12``. Re-running an import therefore updates instead of duplicating,
which is what makes the operation safe to repeat when a first attempt stopped
halfway.

``ImportJob`` is the audit trail of one run: what was asked for, what happened,
and how many rows were created, skipped or rejected. The report is stored as
JSON so the screen can show it again without re-reading the source.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TenantMixin, TimestampMixin

IMPORT_SOURCES = ("manager",)
IMPORT_STATUSES = ("pending", "running", "completed", "failed")
# Entity types an import can map. Kept as a closed list so a typo cannot
# silently write mappings nobody reads back.
IMPORT_ENTITIES = ("contact", "account", "tax_code", "item", "invoice", "payment")


class ImportMapping(Base, TenantMixin, TimestampMixin):
    """External identifier → our row, so a repeat import is an update.

    The unique key is (company, source, entity, external_key): one Manager UUID
    can only ever point at one row of one type in one company, which is exactly
    the guarantee that makes an import idempotent.
    """

    __tablename__ = "import_mappings"
    __table_args__ = (
        UniqueConstraint("company_id", "source", "entity", "external_key", name="uq_import_mappings_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    external_key: Mapped[str] = mapped_column(String(120), nullable=False)
    local_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # A readable label kept for the report ("بنك مصر", "INV-0001"); the id alone
    # would make a dry-run preview unreadable.
    label: Mapped[str] = mapped_column(String(200), default="")
    job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True)


class ImportJob(Base, TenantMixin, TimestampMixin):
    """One import attempt, kept so a failure can be diagnosed afterwards."""

    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default="manager", nullable=False)
    # The Manager "business" the data came from, so a user running several
    # businesses keeps them apart.
    business_key: Mapped[str] = mapped_column(String(120), default="")
    business_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Per-entity counts and the first N problems: {"counts": {...}, "issues": [...]}.
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
