"""Business documents: contacts, invoices, payments, tax codes."""
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

CONTACT_KINDS = ("customer", "supplier", "both")
INVOICE_KINDS = ("sales", "purchase")
INVOICE_STATUSES = ("draft", "issued", "partially_paid", "paid", "void")
PAYMENT_KINDS = ("receive", "spend")


class Contact(Base, TenantMixin, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="customer", nullable=False)
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    tax_number: Mapped[str] = mapped_column(String(60), default="")
    # Portal login: when set, the contact can authenticate and see own documents.
    portal_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxCode(Base, TenantMixin, TimestampMixin):
    """Configurable tax so Egypt's rules (or any country's) are data, not code."""

    __tablename__ = "tax_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    name_ar: Mapped[str] = mapped_column(String(80), default="")
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="vat")  # vat | withholding | other
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Invoice(Base, TenantMixin, TimestampMixin):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_invoices_company_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    number: Mapped[str] = mapped_column(String(40), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base, TenantMixin):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0, nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0, nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)

    # A line may name a stocked item. When it does, issuing the invoice moves
    # stock and books cost of sale; a free-text line (a service, a charge)
    # names no item and touches inventory not at all.
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("item_variants.id"), nullable=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)

    # Cost of this line, frozen when the invoice was issued, split by whether
    # the units came off a work order or from a supplier. Both the quantity and
    # the cost are kept: revenue is apportioned by quantity (every unit sells at
    # the same price), while margin needs the cost. Copied, never derived, so a
    # later change to a cost layer cannot restate a past profit.
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    cost_manufactured: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    cost_purchased: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    quantity_manufactured: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    quantity_purchased: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    invoice = relationship("Invoice", back_populates="lines")
    item = relationship("Item")
    variant = relationship("ItemVariant")


class Payment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_payments_company_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    number: Mapped[str] = mapped_column(String(40), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    reference: Mapped[str] = mapped_column(String(60), default="")
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    contact = relationship("Contact")
    allocations = relationship("PaymentAllocation", back_populates="payment", cascade="all, delete-orphan")


class PaymentAllocation(Base, TenantMixin):
    """Which invoices a payment settled, and how much of each."""

    __tablename__ = "payment_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    payment = relationship("Payment", back_populates="allocations")
    invoice = relationship("Invoice")


class IdempotencyKey(Base, TenantMixin):
    """Records processed write requests so a retried request is a no-op.

    A client sends ``Idempotency-Key``; if we have already stored it we replay
    the original response instead of creating a duplicate document.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("company_id", "key", name="uq_idempotency_company_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(120))
    response_status: Mapped[int] = mapped_column(Integer, default=200)
    response_body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
