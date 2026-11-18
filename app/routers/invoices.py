"""Sales and purchase invoices with automatic double-entry posting."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import account_by_code, delete_entry, money, next_number, post_entry
from ..database import get_db
from ..models import AR_CONTROL, AP_CONTROL, Account, Contact, Invoice, InvoiceLine
from ..schemas import InvoiceIn
from .auth import current_user

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(current_user)])

ZERO = Decimal("0")


def _serialize(inv: Invoice) -> dict:
    return {
        "id": inv.id,
        "kind": inv.kind,
        "number": inv.number,
        "contact_id": inv.contact_id,
        "contact_name": inv.contact.name if inv.contact else "",
        "issue_date": inv.issue_date,
        "due_date": inv.due_date,
        "status": inv.status,
        "notes": inv.notes,
        "subtotal": inv.subtotal,
        "tax_total": inv.tax_total,
        "total": inv.total,
        "amount_paid": inv.amount_paid,
        "balance": money(inv.total) - money(inv.amount_paid),
        "lines": [
            {
                "id": line.id,
                "description": line.description,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "tax_rate": line.tax_rate,
                "account_id": line.account_id,
                "amount": money(line.quantity) * money(line.unit_price),
            }
            for line in inv.lines
        ],
    }


def _compute_totals(payload: InvoiceIn) -> tuple[Decimal, Decimal]:
    subtotal = ZERO
    tax_total = ZERO
    for line in payload.lines:
        amount = money(line.quantity) * money(line.unit_price)
        subtotal += amount
        tax_total += amount * money(line.tax_rate) / Decimal("100")
    return money(subtotal), money(tax_total)


@router.get("")
def list_invoices(kind: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Invoice).order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    if kind:
        stmt = stmt.where(Invoice.kind == kind)
    return [_serialize(inv) for inv in db.scalars(stmt).all()]


@router.get("/{invoice_id}")
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _serialize(inv)


def _post_invoice(db: Session, inv: Invoice) -> None:
    """Turn an issued invoice into a balanced journal entry."""
    contact = db.get(Contact, inv.contact_id)
    control = account_by_code(db, AR_CONTROL if inv.kind == "sales" else AP_CONTROL)
    tax_account = account_by_code(db, "2200")
    default_income = account_by_code(db, "4100" if inv.kind == "sales" else "5100")

    lines: list[dict] = []
    for line in inv.lines:
        amount = money(line.quantity) * money(line.unit_price)
        if amount == ZERO:
            continue
        account_id = line.account_id or (default_income.id if default_income else control.id)
        if inv.kind == "sales":
            lines.append({"account_id": account_id, "credit": amount, "description": line.description})
        else:
            lines.append({"account_id": account_id, "debit": amount, "description": line.description})

    if inv.kind == "sales":
        lines.append({"account_id": control.id, "contact_id": contact.id, "debit": inv.total, "description": f"فاتورة {inv.number}"})
        if money(inv.tax_total):
            lines.append({"account_id": tax_account.id, "credit": money(inv.tax_total), "description": "ضريبة القيمة المضافة"})
    else:
        lines.append({"account_id": control.id, "contact_id": contact.id, "credit": inv.total, "description": f"فاتورة {inv.number}"})
        if money(inv.tax_total):
            lines.append({"account_id": tax_account.id, "debit": money(inv.tax_total), "description": "ضريبة القيمة المضافة"})

    entry = post_entry(
        db,
        entry_date=inv.issue_date,
        lines=lines,
        memo=f"{'Sales' if inv.kind == 'sales' else 'Purchase'} invoice {inv.number}",
        reference=inv.number,
        source_type="invoice",
        source_id=inv.id,
    )
    inv.journal_entry_id = entry.id


@router.post("", status_code=201)
def create_invoice(payload: InvoiceIn, db: Session = Depends(get_db)):
    if not payload.lines:
        raise HTTPException(status_code=400, detail="Invoice needs at least one line")
    contact = db.get(Contact, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    prefix = "INV" if payload.kind == "sales" else "BILL"
    inv = Invoice(
        kind=payload.kind,
        number=next_number(db, Invoice, "kind", payload.kind, prefix),
        contact_id=payload.contact_id,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        notes=payload.notes,
        status="issued",
    )
    subtotal, tax_total = _compute_totals(payload)
    inv.subtotal, inv.tax_total, inv.total = subtotal, tax_total, money(subtotal + tax_total)
    for line in payload.lines:
        inv.lines.append(InvoiceLine(**line.model_dump()))
    db.add(inv)
    db.flush()
    _post_invoice(db, inv)
    db.commit()
    db.refresh(inv)
    return _serialize(inv)


@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if money(inv.amount_paid) > ZERO:
        raise HTTPException(status_code=400, detail="Cannot delete an invoice that has payments")
    delete_entry(db, inv.journal_entry_id)
    db.delete(inv)
    db.commit()
