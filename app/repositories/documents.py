"""Repositories for journal entries, invoices and payments."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..core.errors import NotFoundError
from ..db.base import utcnow
from ..db.models import (
    IdempotencyKey,
    Invoice,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentAllocation,
)


class JournalRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def add_entry(self, entry: JournalEntry) -> JournalEntry:
        entry.company_id = self.company_id
        self.db.add(entry)
        self.db.flush()
        return entry

    def get(self, entry_id: int) -> JournalEntry:
        entry = self.db.scalar(
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.company_id == self.company_id, JournalEntry.id == entry_id)
        )
        if not entry:
            raise NotFoundError("Journal entry not found")
        return entry

    def list(self, start: date | None = None, end: date | None = None, limit: int = 100) -> list[JournalEntry]:
        stmt = (
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.company_id == self.company_id)
            .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
            .limit(limit)
        )
        if start:
            stmt = stmt.where(JournalEntry.entry_date >= start)
        if end:
            stmt = stmt.where(JournalEntry.entry_date <= end)
        return list(self.db.scalars(stmt))

    def lines(self) -> list[JournalLine]:
        return list(
            self.db.scalars(
                select(JournalLine)
                .options(selectinload(JournalLine.account), selectinload(JournalLine.contact))
                .where(JournalLine.company_id == self.company_id)
                .order_by(JournalLine.id)
            )
        )

    def contact_lines(self, contact_id: int) -> list[JournalLine]:
        return list(
            self.db.scalars(
                select(JournalLine)
                .options(selectinload(JournalLine.account), selectinload(JournalLine.entry))
                .where(JournalLine.company_id == self.company_id, JournalLine.contact_id == contact_id)
                .order_by(JournalLine.id)
            )
        )


class InvoiceRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def add(self, invoice: Invoice) -> Invoice:
        invoice.company_id = self.company_id
        self.db.add(invoice)
        self.db.flush()
        return invoice

    def get(self, invoice_id: int) -> Invoice:
        invoice = self.db.scalar(
            select(Invoice)
            .options(selectinload(Invoice.lines), selectinload(Invoice.contact))
            .where(Invoice.company_id == self.company_id, Invoice.id == invoice_id)
        )
        if not invoice:
            raise NotFoundError("Invoice not found")
        return invoice

    def list(self, kind: str | None = None, contact_id: int | None = None) -> list[Invoice]:
        stmt = (
            select(Invoice)
            .options(selectinload(Invoice.lines), selectinload(Invoice.contact))
            .where(Invoice.company_id == self.company_id)
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        )
        if kind:
            stmt = stmt.where(Invoice.kind == kind)
        if contact_id:
            stmt = stmt.where(Invoice.contact_id == contact_id)
        return list(self.db.scalars(stmt))

    def open_for_contact(self, contact_id: int, kind: str) -> list[Invoice]:
        return list(
            self.db.scalars(
                select(Invoice)
                .where(
                    Invoice.company_id == self.company_id,
                    Invoice.contact_id == contact_id,
                    Invoice.kind == kind,
                    Invoice.status.in_(("issued", "partially_paid")),
                )
                .order_by(Invoice.issue_date, Invoice.id)
            )
        )

    def outstanding_invoices(self, kind: str) -> list[Invoice]:
        return list(
            self.db.scalars(
                select(Invoice)
                .options(selectinload(Invoice.contact))
                .where(
                    Invoice.company_id == self.company_id,
                    Invoice.kind == kind,
                    Invoice.status.in_(("issued", "partially_paid")),
                )
                .order_by(Invoice.issue_date, Invoice.id)
            )
        )


class PaymentRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def add(self, payment: Payment) -> Payment:
        payment.company_id = self.company_id
        self.db.add(payment)
        self.db.flush()
        return payment

    def get(self, payment_id: int) -> Payment:
        payment = self.db.scalar(
            select(Payment)
            .options(selectinload(Payment.contact), selectinload(Payment.allocations))
            .where(Payment.company_id == self.company_id, Payment.id == payment_id)
        )
        if not payment:
            raise NotFoundError("Payment not found")
        return payment

    def list(self, kind: str | None = None) -> list[Payment]:
        stmt = (
            select(Payment)
            .options(selectinload(Payment.contact))
            .where(Payment.company_id == self.company_id)
            .order_by(Payment.date.desc(), Payment.id.desc())
        )
        if kind:
            stmt = stmt.where(Payment.kind == kind)
        return list(self.db.scalars(stmt))

    def add_allocation(self, allocation: PaymentAllocation) -> PaymentAllocation:
        allocation.company_id = self.company_id
        self.db.add(allocation)
        self.db.flush()
        return allocation


class IdempotencyRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def find(self, key: str) -> IdempotencyKey | None:
        return self.db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.company_id == self.company_id, IdempotencyKey.key == key
            )
        )

    def store(self, key: str, endpoint: str, status: int, body: str) -> IdempotencyKey:

        record = IdempotencyKey(
            company_id=self.company_id,
            key=key,
            endpoint=endpoint,
            response_status=status,
            response_body=body,
            created_at=utcnow(),
        )
        self.db.add(record)
        self.db.flush()
        return record
