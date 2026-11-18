"""Accounting core: chart of accounts, fiscal periods, journal, sequences.

Design rules enforced here (see services/accounting.py for the logic):

* Journal entries are append-only. A wrong entry is corrected by posting a
  *reversal* that points back at the original; nothing is ever updated or
  deleted. ``JournalEntry`` therefore has no ``updated_at``.
* Every entry belongs to a fiscal period. A closed period rejects new entries.
* Document numbers come from a locked sequence row, so two concurrent
  invoices can never receive the same number.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantMixin, TimestampMixin

# Account types and their normal balance side.
ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
DEBIT_NORMAL_TYPES = ("asset", "expense")

PERIOD_STATUSES = ("open", "closed")

# Control accounts are resolved by code inside a company's chart.
AR_CONTROL_CODE = "1100"
AP_CONTROL_CODE = "2100"
VAT_PAYABLE_CODE = "2200"
VAT_RECEIVABLE_CODE = "1300"
SALES_CODE = "4100"
COGS_CODE = "5100"
CASH_CODES = ("1010", "1020")


class Account(Base, TenantMixin, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_accounts_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(160))
    name_ar: Mapped[str] = mapped_column(String(160), default="")
    type: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    description: Mapped[str] = mapped_column(String(255), default="")

    parent = relationship("Account", remote_side="Account.id", backref="children")


class FiscalPeriod(Base, TenantMixin, TimestampMixin):
    """A month-long accounting period that can be closed to freeze the books."""

    __tablename__ = "fiscal_periods"
    __table_args__ = (UniqueConstraint("company_id", "year", "month", name="uq_periods_company_year_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Sequence(Base, TenantMixin):
    """Gap-free per-company counter for document numbers.

    Read with ``SELECT ... FOR UPDATE`` before incrementing so concurrent
    requests serialise instead of colliding.
    """

    __tablename__ = "sequences"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_sequences_company_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    prefix: Mapped[str] = mapped_column(String(10), default="")
    next_value: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    padding: Mapped[int] = mapped_column(Integer, default=5, nullable=False)


class JournalEntry(Base, TenantMixin):
    """An immutable, balanced accounting entry."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("fiscal_periods.id"), nullable=True)
    reference: Mapped[str] = mapped_column(String(60), default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(30), default="manual")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set when this entry reverses another entry (correction by reversal).
    reverses_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    reversed_by_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    lines = relationship(
        "JournalLine", back_populates="entry", cascade="all, delete-orphan", order_by="JournalLine.id"
    )


class JournalLine(Base, TenantMixin):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")
    contact = relationship("Contact")


class AuditLog(Base, TenantMixin):
    """Append-only trail of who changed what, for compliance."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String(500), default="")
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
