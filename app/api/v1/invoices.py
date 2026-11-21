"""Invoice endpoints with idempotent creation."""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...core.security import DOCUMENT_TOKEN_MINUTES, create_document_token
from ...db.session import get_session
from ...repositories.core import SettingRepository
from ...services.invoices import InvoiceService
from ...services.reports.excel import build_workbook, suggested_filename
from ..v1.deps import Principal, require
from ..v1.idempotency import replay_or_execute
from ..v1.schemas import (
    InvoiceCreate,
    InvoiceOut,
    InvoiceUpdate,
    ReturnableLine,
    ReturnCreate,
    VoidRequest,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The workbook is read by a person, so the document kind is written in Arabic
# rather than as the internal key.
KIND_LABELS_AR = {
    "sales": "فاتورة بيع",
    "purchase": "فاتورة شراء",
    "sales_return": "مرتجع بيع",
    "purchase_return": "مرتجع شراء",
}


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    kind: str | None = None,
    contact_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    return InvoiceService(db, principal.company_id, principal.user.id).list(
        kind=kind, contact_id=contact_id, start=start, end=end
    )


@router.get("/export.xlsx")
def export_invoices(
    kind: str | None = None,
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    """Download the invoice list as a workbook, with the same filters as the screen."""
    invoices = InvoiceService(db, principal.company_id, principal.user.id).list(
        kind=kind, start=start, end=end
    )
    company_name = SettingRepository(db, principal.company_id).get("company_name", "")
    rows = [
        {
            "number": invoice.number,
            "issue_date": invoice.issue_date,
            "kind": KIND_LABELS_AR.get(invoice.kind, invoice.kind),
            "contact": invoice.contact.name if invoice.contact else "",
            "status": invoice.status,
            "subtotal": invoice.subtotal,
            "discount_total": invoice.discount_total,
            "tax_total": invoice.tax_total,
            "total": invoice.total,
            "amount_paid": invoice.amount_paid,
            "currency": invoice.currency or "",
        }
        for invoice in invoices
    ]
    payload = {
        "company": company_name,
        "count": len(rows),
        "invoices": rows,
    }
    return Response(
        content=build_workbook("invoices", payload, title="الفواتير"),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(suggested_filename('invoices', company_name))}"
            )
        },
    )


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: int,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    return InvoiceService(db, principal.company_id, principal.user.id).get(invoice_id)


@router.post("/{invoice_id}/print-token")
def invoice_print_token(
    invoice_id: int,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    """Mint a one-document link the browser can open in a new tab.

    The access token stays out of the URL: it is a session credential and a URL
    ends up in history, logs and anything the user shares. The returned token
    opens this invoice and nothing else, and expires in minutes.
    """
    InvoiceService(db, principal.company_id, principal.user.id).get(invoice_id)
    token = create_document_token(principal.user.id, principal.company_id, "invoice", invoice_id)
    # A path, not an absolute URL: when the SPA is hosted on a static domain the
    # API lives elsewhere, and only the client knows where. It joins this path
    # onto its own API base.
    return {
        "path": f"/documents/invoice/{invoice_id}?t={token}",
        "expires_in": DOCUMENT_TOKEN_MINUTES * 60,
    }


@router.get("/{invoice_id}/returnable", response_model=list[ReturnableLine])
def returnable_lines(
    invoice_id: int,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    """Per line of an invoice: what has been returned and what remains."""
    return InvoiceService(db, principal.company_id, principal.user.id).returnable(invoice_id)


@router.get("/{invoice_id}/returns", response_model=list[InvoiceOut])
def list_returns(
    invoice_id: int,
    principal: Principal = Depends(require(Permission.INVOICE_VIEW)),
    db: Session = Depends(get_session),
):
    return InvoiceService(db, principal.company_id, principal.user.id).returns_of(invoice_id)


@router.post("/{invoice_id}/returns", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_return(
    invoice_id: int,
    payload: ReturnCreate,
    principal: Principal = Depends(require(Permission.INVOICE_MANAGE)),
    db: Session = Depends(get_session),
):
    ret = InvoiceService(db, principal.company_id, principal.user.id).create_return(
        invoice_id=invoice_id,
        issue_date=payload.issue_date,
        lines=[line.model_dump() for line in payload.lines],
        notes=payload.notes,
        status=payload.status,
    )
    db.commit()
    return InvoiceOut.model_validate(ret)


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
