"""Reporting endpoints and fiscal period control.

Two ideas hold this file together.

The first is that a report is *data*, not a route. ``catalog.REPORTS`` lists
every report; ``GET /reports/{key}`` runs whichever one is named, passing only
the filters that report declares it accepts. So adding a report is one entry in
the catalogue and nothing here changes.

The second is that the Excel export is the same payload the screen shows.
``GET /reports/{key}/export.xlsx`` runs the identical report and hands it to the
writer, so an exported figure can never disagree with the one on screen.

Endpoints are declared most-specific first, because ``/{key}`` would otherwise
swallow ``/catalog`` and ``/periods``.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ...core.errors import ConflictError, NotFoundError
from ...core.permissions import Permission
from ...db.base import utcnow
from ...db.session import get_session
from ...repositories.core import PeriodRepository, SettingRepository
from ...services.reports import ReportService
from ...services.reports.catalog import REPORTS_BY_KEY, by_category
from ...services.reports.excel import build_workbook, suggested_filename
from ..v1.deps import Principal, require

router = APIRouter(prefix="/reports", tags=["reports"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _split_lists(spec, filters: dict) -> dict:
    """Turn declared list params from a comma-separated string into a list.

    The pivot takes ``rows=month,party`` on the wire because a repeated query
    parameter is awkward from the client; a single value is accepted too.
    """
    for name in spec.list_params:
        value = filters.get(name)
        if isinstance(value, str):
            filters[name] = [part.strip() for part in value.split(",") if part.strip()]
    return filters


def _run(key: str, principal: Principal, db: Session, **filters) -> dict:
    """Run a catalogued report, passing only the filters it declares."""
    spec = REPORTS_BY_KEY.get(key)
    if spec is None:
        raise NotFoundError(f"تقرير غير معروف: {key}")
    accepted = {name: value for name, value in filters.items() if name in spec.params and value is not None}
    accepted = _split_lists(spec, accepted)
    service = ReportService(db, principal.company_id)
    return getattr(service, spec.method)(**accepted)


# ------------------------------------------------------------- catalogue


@router.get("/catalog")
def catalog(principal: Principal = Depends(require(Permission.REPORT_VIEW))):
    """The reports available, grouped for the menu."""
    return {"categories": by_category(), "count": len(REPORTS_BY_KEY)}


# -------------------------------------------------------- fiscal periods


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
    periods = PeriodRepository(db, principal.company_id)
    period = periods.for_date(date(year, month, 1))
    if period.status == "closed":
        raise ConflictError("الفترة مغلقة بالفعل")
    period.status = "closed"
    period.closed_at = utcnow()
    period.closed_by_id = principal.user.id
    db.commit()
    return {"id": period.id, "year": year, "month": month, "status": "closed"}


@router.post("/periods/{year}/{month}/reopen")
def reopen_period(
    year: int,
    month: int,
    principal: Principal = Depends(require(Permission.PERIOD_MANAGE)),
    db: Session = Depends(get_session),
):
    """Reopen a closed month.

    Closing is a one-click action on a phone, so it must be undoable: without
    this a mistap would lock the books for good and only a DBA could fix it.
    """
    periods = PeriodRepository(db, principal.company_id)
    period = periods.for_date(date(year, month, 1))
    if period.status != "closed":
        raise ConflictError("الفترة مفتوحة بالفعل")
    period.status = "open"
    period.closed_at = None
    period.closed_by_id = None
    db.commit()
    return {"id": period.id, "year": year, "month": month, "status": "open"}


# ------------------------------------------------- reports that need an id


@router.get("/accounts/{account_id}/activity")
def account_activity(
    account_id: int,
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    """One account's lines with a running balance."""
    return ReportService(db, principal.company_id).account_activity(account_id, start=start, end=end)


@router.get("/contacts/{contact_id}/statement")
def contact_statement(
    contact_id: int,
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    """One party's history with a running balance and their open documents."""
    return ReportService(db, principal.company_id).contact_statement(contact_id, start=start, end=end)


# ------------------------------------------------- any catalogued report


@router.get("/{key}/export.xlsx")
def export_report(
    key: str,
    start: date | None = None,
    end: date | None = None,
    upto: date | None = None,
    as_of: date | None = None,
    limit: int | None = None,
    days: int | None = None,
    year: int | None = None,
    warehouse_id: int | None = None,
    rows: str | None = None,
    columns: str | None = None,
    values: str | None = None,
    kind: str | None = None,
    contact_id: int | None = None,
    item_id: int | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    """Download one report as a real ``.xlsx`` workbook."""
    spec = REPORTS_BY_KEY.get(key)
    if spec is None:
        raise NotFoundError(f"تقرير غير معروف: {key}")
    payload = _run(
        key,
        principal,
        db,
        start=start,
        end=end,
        upto=upto,
        as_of=as_of,
        limit=limit,
        days=days,
        year=year,
        warehouse_id=warehouse_id,
        rows=rows,
        columns=columns,
        values=values,
        kind=kind,
        contact_id=contact_id,
        item_id=item_id,
    )
    company_name = SettingRepository(db, principal.company_id).get("company_name", "")
    return Response(
        content=build_workbook(key, payload, title=spec.label),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            # RFC 5987: the filename must be percent-encoded, because HTTP
            # headers are latin-1 and an Arabic name would otherwise raise.
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(suggested_filename(key, company_name))}"
            ),
            "X-Report-Key": key,
        },
    )


@router.get("/{key}")
def run_report(
    key: str,
    start: date | None = None,
    end: date | None = None,
    upto: date | None = None,
    as_of: date | None = None,
    limit: int | None = None,
    days: int | None = None,
    year: int | None = None,
    warehouse_id: int | None = None,
    rows: str | None = None,
    columns: str | None = None,
    values: str | None = None,
    kind: str | None = None,
    contact_id: int | None = None,
    item_id: int | None = None,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    """Run any catalogued report by key, with whichever filters it accepts."""
    return _run(
        key,
        principal,
        db,
        start=start,
        end=end,
        upto=upto,
        as_of=as_of,
        limit=limit,
        days=days,
        year=year,
        warehouse_id=warehouse_id,
        rows=rows,
        columns=columns,
        values=values,
        kind=kind,
        contact_id=contact_id,
        item_id=item_id,
    )
