"""Serving printable documents.

The only route here is opened by a browser tab, not by the SPA, so it
authenticates with a document token in the query string instead of a bearer
header. That token names exactly one document and one company and expires in
minutes, which is what makes a link safe to open in a new tab or send to a
printer queue without exposing the user's session.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.security import decode_document_token
from ...db.models import Account, Company, Contact, Invoice, Payment, User
from ...db.session import get_session
from ...repositories.core import SettingRepository
from ...services.documents import (
    invoice_title,
    payment_title,
    render_invoice,
    render_payment,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# A printed invoice may carry a customer's pricing and the company's bank
# details, so it must never sit in a shared proxy cache.
NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def _unauthorised() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_document_token", "message": "رابط الطباعة منتهي أو غير صالح"},
    )


@router.get("/invoice/{invoice_id}", response_class=HTMLResponse)
def print_invoice(
    invoice_id: int,
    t: str = Query(..., description="Short-lived document token"),
    db: Session = Depends(get_session),
):
    payload = decode_document_token(t, "invoice", invoice_id)
    if payload is None:
        raise _unauthorised()

    company_id = int(payload["cid"])
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorised()

    invoice = db.get(Invoice, invoice_id)
    # Tenant check by hand: the token carries a company id, but the invoice id
    # in the URL is user-supplied, so a token for company A must not open
    # company B's document.
    if invoice is None or invoice.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "الفاتورة غير موجودة"},
        )

    company = db.get(Company, company_id)
    contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    settings = SettingRepository(db, company_id).all()

    html = render_invoice(invoice, contact, company, settings)
    headers = dict(NO_STORE)
    # HTTP headers are latin-1, so an Arabic filename raises UnicodeEncodeError.
    # RFC 5987 percent-encodes it instead — the same trap the .xlsx export hit.
    headers["Content-Disposition"] = (
        f"inline; filename=\"invoice-{invoice.id}.html\"; "
        f"filename*=UTF-8''{quote(invoice_title(invoice) + '.html')}"
    )
    return HTMLResponse(html, headers=headers)


@router.get("/payment/{payment_id}", response_class=HTMLResponse)
def print_payment(
    payment_id: int,
    t: str = Query(..., description="Short-lived document token"),
    db: Session = Depends(get_session),
):
    payload = decode_document_token(t, "payment", payment_id)
    if payload is None:
        raise _unauthorised()

    company_id = int(payload["cid"])
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorised()

    payment = db.get(Payment, payment_id)
    if payment is None or payment.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "السند غير موجود"},
        )

    company = db.get(Company, company_id)
    contact = db.get(Contact, payment.contact_id) if payment.contact_id else None
    settings = SettingRepository(db, company_id).all()
    bank = db.get(Account, payment.bank_account_id) if payment.bank_account_id else None

    # The voucher lists which invoices it settled; resolve their numbers in one
    # query so the page can print a reference instead of a bare id.
    invoice_refs: dict[int, tuple[str, date | None]] = {}
    invoice_ids = [a.invoice_id for a in payment.allocations]
    if invoice_ids:
        rows = db.scalars(
            select(Invoice).where(Invoice.company_id == company_id, Invoice.id.in_(invoice_ids))
        )
        invoice_refs = {row.id: (row.number, row.issue_date) for row in rows}

    html = render_payment(
        payment,
        contact,
        company,
        settings,
        bank_account_name=(bank.name_ar or bank.name) if bank else "",
        invoices=invoice_refs,
    )
    headers = dict(NO_STORE)
    headers["Content-Disposition"] = (
        f"inline; filename=\"payment-{payment.id}.html\"; "
        f"filename*=UTF-8''{quote(payment_title(payment) + '.html')}"
    )
    return HTMLResponse(html, headers=headers)
