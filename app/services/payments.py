"""Payment service: record money received/spent and allocate it to invoices."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..core.errors import ValidationError
from ..db.models import AP_CONTROL_CODE, AR_CONTROL_CODE, Invoice, Payment, PaymentAllocation
from ..domain.money import EntryLine, to_money
from ..repositories.core import AccountRepository, AuditRepository, ContactRepository, SequenceRepository
from ..repositories.documents import InvoiceRepository, PaymentRepository
from .accounting import AccountingService

ZERO = Decimal("0.00")

# Payment direction is stored in English; the audit trail shows the Arabic word.
PAYMENT_KIND_AR = {"receive": "سند قبض", "spend": "سند دفع"}


class PaymentService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.payments = PaymentRepository(db, company_id)
        self.invoices = InvoiceRepository(db, company_id)
        self.accounts = AccountRepository(db, company_id)
        self.contacts = ContactRepository(db, company_id)
        self.sequences = SequenceRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)
        self.accounting = AccountingService(db, company_id, user_id)

    def list(self, kind: str | None = None) -> list[Payment]:
        return self.payments.list(kind=kind)

    def create(
        self,
        *,
        kind: str,
        contact_id: int,
        payment_date: date,
        amount,
        bank_account_id: int,
        reference: str = "",
        invoice_id: int | None = None,
    ) -> Payment:
        if kind not in ("receive", "spend"):
            raise ValidationError("Payment kind must be receive or spend")
        amount = to_money(amount)
        if amount <= ZERO:
            raise ValidationError("Amount must be positive")

        contact = self.contacts.get(contact_id)
        bank = self.accounts.get(bank_account_id)
        control = self.accounts.get_by_code(AR_CONTROL_CODE if kind == "receive" else AP_CONTROL_CODE)

        number = self.sequences.next_number(f"payment_{kind}", prefix="RCV" if kind == "receive" else "PAY")
        payment = Payment(
            company_id=self.company_id,
            kind=kind,
            number=number,
            contact_id=contact.id,
            date=payment_date,
            amount=amount,
            bank_account_id=bank.id,
            reference=reference,
            created_by_id=self.user_id,
        )
        self.payments.add(payment)

        if kind == "receive":
            lines = [
                EntryLine(account_id=bank.id, debit=amount, description="قبض نقدية"),
                EntryLine(
                    account_id=control.id,
                    contact_id=contact.id,
                    credit=amount,
                    description=f"سداد من {contact.name}",
                ),
            ]
        else:
            lines = [
                EntryLine(
                    account_id=control.id,
                    contact_id=contact.id,
                    debit=amount,
                    description=f"سداد إلى {contact.name}",
                ),
                EntryLine(account_id=bank.id, credit=amount, description="دفع نقدية"),
            ]

        entry = self.accounting.post(
            entry_date=payment_date,
            lines=lines,
            memo=f"Payment {number}",
            reference=number,
            source_type="payment",
            source_id=payment.id,
        )
        payment.journal_entry_id = entry.id
        self._allocate(payment, preferred_invoice_id=invoice_id)
        self.audit.record(
            action="payment.create",
            entity_type="payment",
            entity_id=payment.id,
            user_id=self.user_id,
            summary=f"{PAYMENT_KIND_AR.get(kind, kind)} {number} بمبلغ {amount} لـ {contact.name}",
        )
        return payment

    def _allocate(self, payment: Payment, preferred_invoice_id: int | None = None) -> None:
        """Settle the contact's open invoices, oldest first (FIFO).

        Allocation is explicit and stored, so a statement can explain exactly
        which invoice each payment closed — instead of recomputing it later and
        risking a different answer. When the caller names an invoice (the user
        paid *this* bill) it is settled first, then the remainder falls back to
        FIFO so an over-payment still lands somewhere sensible.
        """
        invoice_kind = "sales" if payment.kind == "receive" else "purchase"
        remaining = to_money(payment.amount)
        open_invoices = list(self.invoices.open_for_contact(payment.contact_id, invoice_kind))

        if preferred_invoice_id is not None:
            preferred = next((inv for inv in open_invoices if inv.id == preferred_invoice_id), None)
            if preferred is None:
                raise ValidationError("Invoice not found or already settled for this contact")
            open_invoices = [preferred, *(inv for inv in open_invoices if inv.id != preferred_invoice_id)]

        for invoice in open_invoices:
            if remaining <= ZERO:
                break
            outstanding = to_money(invoice.total) - to_money(invoice.amount_paid)
            if outstanding <= ZERO:
                continue
            applied = min(outstanding, remaining)
            invoice.amount_paid = to_money(invoice.amount_paid) + applied
            remaining -= applied
            self.payments.add_allocation(
                PaymentAllocation(
                    company_id=self.company_id,
                    payment_id=payment.id,
                    invoice_id=invoice.id,
                    amount=applied,
                )
            )
            self._refresh_status(invoice)
        self.db.flush()

    @staticmethod
    def _refresh_status(invoice: Invoice) -> None:
        paid = to_money(invoice.amount_paid)
        total = to_money(invoice.total)
        if paid >= total and total > ZERO:
            invoice.status = "paid"
        elif paid > ZERO:
            invoice.status = "partially_paid"
        else:
            invoice.status = "issued"

    def reverse(self, payment_id: int, reason: str = "") -> None:
        """Undo a payment: reverse its entry and un-allocate it from invoices."""
        payment = self.payments.get(payment_id)
        if payment.journal_entry_id:
            self.accounting.reverse(payment.journal_entry_id, reason=reason or f"reverse {payment.number}")

        for allocation in payment.allocations:
            invoice = self.invoices.get(allocation.invoice_id)
            invoice.amount_paid = to_money(invoice.amount_paid) - to_money(allocation.amount)
            if invoice.amount_paid < ZERO:
                invoice.amount_paid = ZERO
            self._refresh_status(invoice)
            self.db.delete(allocation)

        self.audit.record(
            action="payment.reverse",
            entity_type="payment",
            entity_id=payment.id,
            user_id=self.user_id,
            summary=f"عكس السند {payment.number}: {reason}",
        )
        self.db.flush()
