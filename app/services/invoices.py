"""Invoice service: create, issue and void sales/purchase invoices.

An invoice becomes an accounting fact when issued: at that moment a balanced
journal entry is posted and linked back to the document. Voiding posts a
reversal instead of deleting anything.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..core.errors import ImmutableRecordError, ValidationError
from ..db.base import utcnow
from ..db.models import (
    AP_CONTROL_CODE,
    AR_CONTROL_CODE,
    COGS_CODE,
    SALES_CODE,
    VAT_PAYABLE_CODE,
    Invoice,
    InvoiceLine,
)
from ..domain.money import EntryLine, compute_line, summarize, to_money
from ..repositories.core import AccountRepository, AuditRepository, ContactRepository, SequenceRepository
from ..repositories.documents import InvoiceRepository
from .accounting import AccountingService

ZERO = Decimal("0.00")


class InvoiceService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.invoices = InvoiceRepository(db, company_id)
        self.accounts = AccountRepository(db, company_id)
        self.contacts = ContactRepository(db, company_id)
        self.sequences = SequenceRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)
        self.accounting = AccountingService(db, company_id, user_id)

    def list(self, kind: str | None = None, contact_id: int | None = None) -> list[Invoice]:
        return self.invoices.list(kind=kind, contact_id=contact_id)

    def get(self, invoice_id: int) -> Invoice:
        return self.invoices.get(invoice_id)

    def create(
        self,
        *,
        kind: str,
        contact_id: int,
        issue_date: date,
        due_date: date | None,
        lines: list[dict],
        notes: str = "",
        status: str = "issued",
    ) -> Invoice:
        if kind not in ("sales", "purchase"):
            raise ValidationError("Invoice kind must be sales or purchase")
        if not lines:
            raise ValidationError("Invoice needs at least one line")

        contact = self.contacts.get(contact_id)
        computed = [
            compute_line(
                line.get("quantity", 1),
                line.get("unit_price", 0),
                line.get("discount_rate", 0),
                line.get("tax_rate", 0),
            )
            for line in lines
        ]
        totals = summarize(computed)

        prefix = "INV" if kind == "sales" else "BILL"
        number = self.sequences.next_number(f"invoice_{kind}", prefix=prefix)

        invoice = Invoice(
            company_id=self.company_id,
            kind=kind,
            number=number,
            contact_id=contact.id,
            issue_date=issue_date,
            due_date=due_date,
            status=status,
            notes=notes,
            currency=contact_company_currency(contact, self.company_id),
            subtotal=totals["subtotal"],
            discount_total=totals["discount_total"],
            tax_total=totals["tax_total"],
            total=totals["total"],
            created_by_id=self.user_id,
            issued_at=utcnow() if status == "issued" else None,
        )
        for raw, _amounts in zip(lines, computed, strict=True):
            invoice.lines.append(
                InvoiceLine(
                    company_id=self.company_id,
                    description=raw.get("description", ""),
                    quantity=raw.get("quantity", 1),
                    unit_price=raw.get("unit_price", 0),
                    discount_rate=raw.get("discount_rate", 0),
                    tax_rate=raw.get("tax_rate", 0),
                    account_id=raw.get("account_id"),
                )
            )
        self.invoices.add(invoice)

        if status == "issued":
            self._post(invoice)
        self.audit.record(
            action="invoice.create",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=self.user_id,
            summary=f"{kind} invoice {number} for {contact.name}",
        )
        return invoice

    def void(self, invoice_id: int, reason: str = "") -> Invoice:
        invoice = self.invoices.get(invoice_id)
        if invoice.status == "void":
            raise ImmutableRecordError("Invoice is already void")
        if to_money(invoice.amount_paid) > ZERO:
            raise ImmutableRecordError("Cannot void an invoice that has payments; reverse the payments first")
        if invoice.journal_entry_id:
            self.accounting.reverse(invoice.journal_entry_id, reason=reason or f"void {invoice.number}")
        invoice.status = "void"
        self.audit.record(
            action="invoice.void",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=self.user_id,
            summary=f"Voided {invoice.number}: {reason}",
        )
        self.db.flush()
        return invoice

    def _post(self, invoice: Invoice) -> None:
        control = self.accounts.get_by_code(AR_CONTROL_CODE if invoice.kind == "sales" else AP_CONTROL_CODE)
        vat = self.accounts.by_code(VAT_PAYABLE_CODE)
        default_account = self.accounts.by_code(SALES_CODE if invoice.kind == "sales" else COGS_CODE)

        lines: list[EntryLine] = []
        for line in invoice.lines:
            amounts = compute_line(line.quantity, line.unit_price, line.discount_rate, line.tax_rate)
            if amounts.net == ZERO:
                continue
            account_id = line.account_id or (default_account.id if default_account else control.id)
            if invoice.kind == "sales":
                lines.append(EntryLine(account_id=account_id, credit=amounts.net, description=line.description))
            else:
                lines.append(EntryLine(account_id=account_id, debit=amounts.net, description=line.description))

        if invoice.kind == "sales":
            lines.append(
                EntryLine(
                    account_id=control.id,
                    contact_id=invoice.contact_id,
                    debit=invoice.total,
                    description=f"فاتورة {invoice.number}",
                )
            )
            if to_money(invoice.tax_total) and vat:
                lines.append(
                    EntryLine(account_id=vat.id, credit=invoice.tax_total, description="ضريبة القيمة المضافة")
                )
        else:
            lines.append(
                EntryLine(
                    account_id=control.id,
                    contact_id=invoice.contact_id,
                    credit=invoice.total,
                    description=f"فاتورة {invoice.number}",
                )
            )
            if to_money(invoice.tax_total) and vat:
                lines.append(
                    EntryLine(account_id=vat.id, debit=invoice.tax_total, description="ضريبة القيمة المضافة")
                )

        entry = self.accounting.post(
            entry_date=invoice.issue_date,
            lines=lines,
            memo=f"{'Sales' if invoice.kind == 'sales' else 'Purchase'} invoice {invoice.number}",
            reference=invoice.number,
            source_type="invoice",
            source_id=invoice.id,
        )
        invoice.journal_entry_id = entry.id
        self.db.flush()


def contact_company_currency(contact, company_id: int) -> str:
    """Invoices inherit the company's base currency for now.

    Kept as a function so multi-currency can change the rule in one place.
    """
    return "EGP"
