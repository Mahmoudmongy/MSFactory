"""Payments received from customers and paid to suppliers."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import account_by_code, delete_entry, money, next_number, post_entry
from ..database import get_db
from ..models import AR_CONTROL, AP_CONTROL, Contact, Invoice, Payment
from ..schemas import PaymentIn
from .auth import current_user

router = APIRouter(prefix="/api/payments", tags=["payments"], dependencies=[Depends(current_user)])


def _serialize(p: Payment) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "number": p.number,
        "contact_id": p.contact_id,
        "contact_name": p.contact.name if p.contact else "",
        "date": p.date,
        "amount": p.amount,
        "bank_account_id": p.bank_account_id,
        "reference": p.reference,
    }


@router.get("")
def list_payments(kind: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Payment).order_by(Payment.date.desc(), Payment.id.desc())
    if kind:
        stmt = stmt.where(Payment.kind == kind)
    return [_serialize(p) for p in db.scalars(stmt).all()]


def _allocate(db: Session, payment: Payment) -> None:
    """Apply a payment to the oldest open invoices first."""
    inv_kind = "sales" if payment.kind == "receive" else "purchase"
    open_invoices = db.scalars(
        select(Invoice)
        .where(Invoice.kind == inv_kind, Invoice.contact_id == payment.contact_id, Invoice.status.in_(("issued", "paid")))
        .order_by(Invoice.issue_date, Invoice.id)
    ).all()
    remaining = money(payment.amount)
    for inv in open_invoices:
        if remaining <= 0:
            break
        outstanding = money(inv.total) - money(inv.amount_paid)
        if outstanding <= 0:
            continue
        applied = min(outstanding, remaining)
        inv.amount_paid = money(inv.amount_paid) + applied
        remaining -= applied
        if money(inv.amount_paid) >= money(inv.total):
            inv.status = "paid"


@router.post("", status_code=201)
def create_payment(payload: PaymentIn, db: Session = Depends(get_db)):
    contact = db.get(Contact, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    control = account_by_code(db, AR_CONTROL if payload.kind == "receive" else AP_CONTROL)
    payment = Payment(
        kind=payload.kind,
        number=next_number(db, Payment, "kind", payload.kind, "RCV" if payload.kind == "receive" else "PAY"),
        contact_id=payload.contact_id,
        date=payload.date,
        amount=money(payload.amount),
        bank_account_id=payload.bank_account_id,
        reference=payload.reference,
    )
    db.add(payment)
    db.flush()

    if payload.kind == "receive":
        lines = [
            {"account_id": payload.bank_account_id, "debit": money(payload.amount), "description": "قبض نقدية"},
            {"account_id": control.id, "contact_id": contact.id, "credit": money(payload.amount), "description": f"سداد من {contact.name}"},
        ]
    else:
        lines = [
            {"account_id": control.id, "contact_id": contact.id, "debit": money(payload.amount), "description": f"سداد إلى {contact.name}"},
            {"account_id": payload.bank_account_id, "credit": money(payload.amount), "description": "دفع نقدية"},
        ]
    entry = post_entry(
        db,
        entry_date=payload.date,
        lines=lines,
        memo=f"Payment {payment.number}",
        reference=payment.number,
        source_type="payment",
        source_id=payment.id,
    )
    payment.journal_entry_id = entry.id
    _allocate(db, payment)
    db.commit()
    db.refresh(payment)
    return _serialize(payment)


@router.delete("/{payment_id}", status_code=204)
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    inv_kind = "sales" if payment.kind == "receive" else "purchase"
    for inv in db.scalars(
        select(Invoice).where(Invoice.kind == inv_kind, Invoice.contact_id == payment.contact_id)
    ).all():
        inv.amount_paid = money(inv.amount_paid) - money(payment.amount)
        if money(inv.amount_paid) < 0:
            inv.amount_paid = Decimal("0")
        inv.status = "paid" if money(inv.amount_paid) >= money(inv.total) and money(inv.total) > 0 else "issued"
    delete_entry(db, payment.journal_entry_id)
    db.delete(payment)
    db.commit()
