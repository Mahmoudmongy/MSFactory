"""Manual journal entry endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.session import get_session
from ...domain.money import EntryLine
from ...repositories.documents import JournalRepository
from ...services.accounting import AccountingService
from ..v1.deps import Principal, require
from ..v1.schemas import JournalCreate, JournalOut, ReverseRequest

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("", response_model=list[JournalOut])
def list_entries(
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return JournalRepository(db, principal.company_id).list(start=start, end=end, limit=limit)


@router.post("", response_model=JournalOut, status_code=status.HTTP_201_CREATED)
def create_entry(
    payload: JournalCreate,
    principal: Principal = Depends(require(Permission.JOURNAL_MANAGE)),
    db: Session = Depends(get_session),
):
    lines = [
        EntryLine(
            account_id=line.account_id,
            debit=line.debit,
            credit=line.credit,
            contact_id=line.contact_id,
            description=line.description,
        )
        for line in payload.lines
    ]
    entry = AccountingService(db, principal.company_id, principal.user.id).post(
        entry_date=payload.entry_date,
        lines=lines,
        memo=payload.memo,
        reference=payload.reference,
        source_type="manual",
    )
    db.commit()
    return JournalRepository(db, principal.company_id).get(entry.id)


@router.post("/{entry_id}/reverse", response_model=JournalOut)
def reverse_entry(
    entry_id: int,
    payload: ReverseRequest,
    principal: Principal = Depends(require(Permission.JOURNAL_MANAGE)),
    db: Session = Depends(get_session),
):
    reversal = AccountingService(db, principal.company_id, principal.user.id).reverse(
        entry_id, reason=payload.reason, reversal_date=payload.reversal_date
    )
    db.commit()
    return JournalRepository(db, principal.company_id).get(reversal.id)
