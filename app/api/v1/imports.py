"""Import a business from Manager.io.

Three steps, in this order, because each one depends on the previous:

1. ``POST /imports/manager/businesses`` — list what the API key can see, so the
   user picks the right business instead of pasting a UUID from memory.
2. ``POST /imports/manager/preview`` — a dry run. It walks the real import path
   and rolls back, returning the counts and the problems it would hit.
3. ``POST /imports/manager/run`` — the real thing.

The API key is never stored: it is used for the duration of one request and
then dropped. It is not written to the database, the audit log or the job row,
so a leak of the database cannot leak a customer's Manager credentials.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.session import get_session
from ...services.imports import ImportOptions, ManagerImportService
from ...services.manager_client import DEFAULT_BASE_URL, ManagerClient, ManagerCredentials
from ..v1.deps import Principal, require

router = APIRouter(prefix="/imports/manager", tags=["imports"])


class ManagerCredentialsIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)
    business: str = Field(default="", max_length=120)
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=200)


class ImportOptionsIn(BaseModel):
    contacts: bool = True
    accounts: bool = True
    tax_codes: bool = True
    items: bool = True
    invoices: bool = True
    payments: bool = True
    since: date | None = None


class ImportRequest(BaseModel):
    credentials: ManagerCredentialsIn
    options: ImportOptionsIn = ImportOptionsIn()


def _client(payload: ManagerCredentialsIn) -> ManagerClient:
    return ManagerClient(
        ManagerCredentials(
            base_url=payload.base_url.strip() or DEFAULT_BASE_URL,
            api_key=payload.api_key.strip(),
            business=payload.business.strip(),
        )
    )


@router.post("/businesses")
def list_businesses(
    payload: ManagerCredentialsIn,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
):
    """The businesses this key can read, so the user can pick one by name."""
    client = _client(payload)
    rows = client.businesses()
    return [
        {
            "key": row["key"],
            "name": row["item"].get("name") or row["item"].get("businessName") or row["key"],
        }
        for row in rows
        if row["key"]
    ]


@router.post("/preview")
def preview(
    payload: ImportRequest,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    """Dry run: what would be created, and what would be refused."""
    service = ManagerImportService(db, principal.company_id, principal.user.id, client=_client(payload.credentials))
    return _report(service.run(_options(payload.options), dry_run=True))


@router.post("/run")
def run(
    payload: ImportRequest,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    service = ManagerImportService(db, principal.company_id, principal.user.id, client=_client(payload.credentials))
    return _report(service.run(_options(payload.options), dry_run=False))


@router.get("/jobs")
def list_jobs(
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    """Past imports, newest first — so a half-finished migration is visible."""
    from sqlalchemy import select

    from ...db.models import ImportJob

    rows = db.scalars(
        select(ImportJob).where(ImportJob.company_id == principal.company_id).order_by(ImportJob.id.desc()).limit(20)
    )
    return [
        {
            "id": job.id,
            "status": job.status,
            "dry_run": job.dry_run,
            "business_name": job.business_name,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "report": job.report,
            "error": job.error,
        }
        for job in rows
    ]


def _options(raw: ImportOptionsIn) -> ImportOptions:
    return ImportOptions(
        contacts=raw.contacts,
        accounts=raw.accounts,
        tax_codes=raw.tax_codes,
        items=raw.items,
        invoices=raw.invoices,
        payments=raw.payments,
        since=raw.since,
    )


def _report(result) -> dict:
    return {
        "job_id": result.job_id,
        "status": result.status,
        "dry_run": result.dry_run,
        "counts": result.counts,
        "issues": result.issues,
        "error": result.error,
    }
