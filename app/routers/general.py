"""Manual journal entries and app settings."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import delete_entry, post_entry
from ..database import get_db
from ..models import JournalEntry, Setting
from ..schemas import JournalIn
from .auth import current_user

router = APIRouter(prefix="/api", tags=["general"], dependencies=[Depends(current_user)])


@router.post("/journal", status_code=201)
def create_journal(payload: JournalIn, db: Session = Depends(get_db)):
    entry = post_entry(
        db,
        entry_date=payload.date,
        lines=[line.model_dump() for line in payload.lines],
        memo=payload.memo,
        reference=payload.reference,
        source_type="manual",
    )
    db.commit()
    db.refresh(entry)
    return {"id": entry.id}


@router.delete("/journal/{entry_id}", status_code=204)
def delete_journal(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(JournalEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.source_type != "manual":
        raise HTTPException(status_code=400, detail="Only manual entries can be deleted here")
    delete_entry(db, entry_id)
    db.commit()


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {s.key: s.value for s in db.scalars(select(Setting)).all()}


@router.put("/settings")
def update_settings(payload: dict[str, str], db: Session = Depends(get_db)):
    for key, value in payload.items():
        setting = db.get(Setting, key)
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.commit()
    return {s.key: s.value for s in db.scalars(select(Setting)).all()}
