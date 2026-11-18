"""Customer / supplier endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import contact_balance
from ..database import get_db
from ..models import Contact
from ..schemas import ContactIn, ContactOut
from .auth import current_user

router = APIRouter(prefix="/api/contacts", tags=["contacts"], dependencies=[Depends(current_user)])


@router.get("")
def list_contacts(kind: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Contact).order_by(Contact.name)
    if kind:
        stmt = stmt.where(Contact.kind.in_((kind, "both")))
    return [
        {**ContactOut.model_validate(c).model_dump(), "balance": contact_balance(db, c.id)}
        for c in db.scalars(stmt).all()
    ]


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactIn, db: Session = Depends(get_db)):
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.put("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, payload: ContactIn, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    for key, value in payload.model_dump().items():
        setattr(contact, key, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/{contact_id}/statement")
def statement(contact_id: int, db: Session = Depends(get_db)):
    from ..models import JournalEntry, JournalLine

    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    lines = db.scalars(
        select(JournalLine)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(JournalLine.contact_id == contact_id)
        .order_by(JournalEntry.date, JournalLine.id)
    ).all()
    running = 0
    rows = []
    for line in lines:
        running += float(line.debit) - float(line.credit)
        rows.append(
            {
                "date": line.entry.date,
                "reference": line.entry.reference,
                "description": line.description or line.entry.memo,
                "debit": line.debit,
                "credit": line.credit,
                "balance": round(running, 2),
            }
        )
    return {"contact": ContactOut.model_validate(contact), "rows": rows, "balance": round(running, 2)}
