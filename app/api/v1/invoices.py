"""Invoice endpoints with idempotent creation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.session import get_session
from ...services.invoices import InvoiceService
from ..v1.deps import Principal, require
from ..v1.idempotency import replay_or_execute
from ..v1.schemas import InvoiceCreate, InvoiceOut, InvoiceUpdate, VoidRequest

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    kind: str | None = None,
    contact_id: int | None = None,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    return InvoiceService(db, principal.company_id, principal.user.id).list(kind=kind, contact_id=contact_id)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: int,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    return InvoiceService(db, principal.company_id, principal.user.id).get(invoice_id)


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require(Permission.INVOICE_MANAGE)),
    db: Session = Depends(get_session),
):
    def run() -> InvoiceOut:
        invoice = InvoiceService(db, principal.company_id, principal.user.id).create(
            kind=payload.kind,
            contact_id=payload.contact_id,
            issue_date=payload.issue_date,
            due_date=payload.due_date,
            lines=[line.model_dump() for line in payload.lines],
            notes=payload.notes,
            status=payload.status,
        )
        db.commit()
        return InvoiceOut.model_validate(invoice)

    return replay_or_execute(db, principal.company_id, idempotency_key, "/invoices", run, InvoiceOut)


@router.patch("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    principal: Principal = Depends(require(Permission.INVOICE_MANAGE)),
    db: Session = Depends(get_session),
):
    invoice = InvoiceService(db, principal.company_id, principal.user.id).update(
        invoice_id,
        kind=payload.kind,
        contact_id=payload.contact_id,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        lines=[line.model_dump() for line in payload.lines] if payload.lines is not None else None,
        notes=payload.notes,
    )
    db.commit()
    return InvoiceOut.model_validate(invoice)


@router.post("/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(
    invoice_id: int,
    principal: Principal = Depends(require(Permission.INVOICE_MANAGE)),
    db: Session = Depends(get_session),
):
    invoice = InvoiceService(db, principal.company_id, principal.user.id).issue(invoice_id)
    db.commit()
    return InvoiceOut.model_validate(invoice)


@router.post("/{invoice_id}/void", response_model=InvoiceOut)
def void_invoice(
    invoice_id: int,
    payload: VoidRequest,
    principal: Principal = Depends(require(Permission.INVOICE_MANAGE)),
    db: Session = Depends(get_session),
):
    invoice = InvoiceService(db, principal.company_id, principal.user.id).void(invoice_id, reason=payload.reason)
    db.commit()
    return InvoiceOut.model_validate(invoice)
