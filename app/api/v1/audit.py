"""Audit trail endpoint: the company's timeline of changes."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.session import get_session
from ...services.audit import AuditService
from ..v1.deps import Principal, require
from ..v1.schemas import AuditPage

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditPage)
def list_audit_logs(
    search: str = "",
    action: str = "",
    user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require(Permission.AUDIT_VIEW)),
    db: Session = Depends(get_session),
):
    return AuditService(db, principal.company_id).list(
        search=search,
        action=action,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/actions", response_model=list[str])
def list_actions(
    principal: Principal = Depends(require(Permission.AUDIT_VIEW)),
    db: Session = Depends(get_session),
):
    return AuditService(db, principal.company_id).actions()
