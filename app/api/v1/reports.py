"""Reporting endpoints and fiscal period control."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...core.errors import ConflictError
from ...core.permissions import Permission
from ...db.base import utcnow
from ...db.session import get_session
from ...repositories.core import PeriodRepository
from ...services.reports import ReportService
from ..v1.deps import Principal, require

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/dashboard")
def dashboard(
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return ReportService(db, principal.company_id).dashboard()


@router.get("/trial-balance")
def trial_balance(
    upto: date | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return ReportService(db, principal.company_id).trial_balance(upto=upto)


@router.get("/income-statement")
def income_statement(
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return ReportService(db, principal.company_id).income_statement(start=start, end=end)


@router.get("/balance-sheet")
def balance_sheet(
    upto: date | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return ReportService(db, principal.company_id).balance_sheet(upto=upto)


@router.get("/ar-aging")
def ar_aging(
    upto: date | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return ReportService(db, principal.company_id).ar_aging(upto=upto)


@router.get("/periods")
def list_periods(
    year: int | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    periods = PeriodRepository(db, principal.company_id).list(year=year)
    return [
        {"id": p.id, "year": p.year, "month": p.month, "status": p.status, "closed_at": p.closed_at}
        for p in periods
    ]


@router.post("/periods/{year}/{month}/close")
def close_period(
    year: int,
    month: int,
    principal: Principal = Depends(require(Permission.PERIOD_MANAGE)),
    db: Session = Depends(get_session),
):
    from datetime import date as date_cls

    periods = PeriodRepository(db, principal.company_id)
    period = periods.for_date(date_cls(year, month, 1))
    if period.status == "closed":
        raise ConflictError("الفترة مغلقة بالفعل")
    period.status = "closed"
    period.closed_at = utcnow()
    period.closed_by_id = principal.user.id
    db.commit()
    return {"id": period.id, "year": year, "month": month, "status": "closed"}
