"""Contact (customer/supplier) endpoints, including statements."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.models import Contact
from ...db.session import get_session
from ...repositories.core import ContactRepository
from ...repositories.documents import InvoiceRepository, JournalRepository, PaymentRepository
from ..v1.deps import Principal, require
from ..v1.schemas import ContactCreate, ContactOut, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactOut])
def list_contacts(
    kind: str | None = None,
    principal: Principal = Depends(require(Permission.CONTACT_VIEW)),
    db: Session = Depends(get_session),
):
    return ContactRepository(db, principal.company_id).list(kind=kind)


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: ContactCreate,
    principal: Principal = Depends(require(Permission.CONTACT_MANAGE)),
    db: Session = Depends(get_session),
):
    contact = Contact(**payload.model_dump())
    result = ContactRepository(db, principal.company_id).add(contact)
    db.commit()
    return result


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    principal: Principal = Depends(require(Permission.CONTACT_MANAGE)),
    db: Session = Depends(get_session),
):
    contact = ContactRepository(db, principal.company_id).get(contact_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    db.commit()
    return contact


@router.get("/{contact_id}/statement")
def contact_statement(
    contact_id: int,
    principal: Principal = Depends(require(Permission.CONTACT_VIEW)),
    db: Session = Depends(get_session),
):
    """Every document and journal line touching this contact, plus a running balance."""
    contacts = ContactRepository(db, principal.company_id)
    contact = contacts.get(contact_id)
    invoices = InvoiceRepository(db, principal.company_id).list(contact_id=contact_id)
    payments = [p for p in PaymentRepository(db, principal.company_id).list() if p.contact_id == contact_id]

    running = 0.0
    movements = []
    for line in JournalRepository(db, principal.company_id).contact_lines(contact_id):
        running += float(line.debit) - float(line.credit)
        movements.append(
            {
                "date": line.entry.entry_date.isoformat() if line.entry else "",
                "reference": line.entry.reference if line.entry else "",
                "description": line.description,
                "debit": float(line.debit),
                "credit": float(line.credit),
                "balance": round(running, 2),
            }
        )
    return {
        "contact": ContactOut.model_validate(contact),
        "balance": contacts.balance(contact_id),
        "movements": movements,
        "invoices": [
            {"id": i.id, "number": i.number, "kind": i.kind, "total": i.total, "status": i.status}
            for i in invoices
        ],
        "payments": [
            {"id": p.id, "number": p.number, "amount": p.amount, "date": p.date.isoformat()}
            for p in payments
        ],
    }
