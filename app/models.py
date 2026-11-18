"""Accounting data model.

The design mirrors how double-entry bookkeeping works:

* Every financial event (invoice, payment, expense...) posts a *JournalEntry*
  made of *JournalLine* rows whose debits must equal credits.
* Accounts form a tree (chart of accounts). Balances are always derived from
  the journal lines, never stored, so reports can never drift out of sync.
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

from .database import Base

# Account types drive which side increases the balance.
ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
# Types that normally carry a debit balance (used by reports).
DEBIT_TYPES = {"asset", "expense"}
# Journal lines that touch one of these control accounts are tagged with a
# contact, which is what makes customer/supplier ledgers possible.
AR_CONTROL = "1100"  # Accounts receivable
AP_CONTROL = "2100"  # Accounts payable


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("code", name="uq_account_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(160))
    name_ar: Mapped[str] = mapped_column(String(160), default="")
    type: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")

    parent = relationship("Account", remote_side="Account.id", backref="children")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # customer | supplier | both
    email: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    tax_number: Mapped[str] = mapped_column(String(60), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    reference: Mapped[str] = mapped_column(String(60), default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(30), default="manual")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lines = relationship(
        "JournalLine", back_populates="entry", cascade="all, delete-orphan", order_by="JournalLine.id"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")
    contact = relationship("Contact")


class TaxCode(Base):
    __tablename__ = "tax_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # sales | purchase
    number: Mapped[str] = mapped_column(String(40), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|issued|paid|void
    notes: Mapped[str] = mapped_column(Text, default="")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    contact = relationship("Contact")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)

    invoice = relationship("Invoice", back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # receive | spend
    number: Mapped[str] = mapped_column(String(40), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    reference: Mapped[str] = mapped_column(String(60), default="")
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    contact = relationship("Contact")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
