"""Serving printable documents.

The only route here is opened by a browser tab, not by the SPA, so it
authenticates with a document token in the query string instead of a bearer
header. That token names exactly one document and one company and expires in
minutes, which is what makes a link safe to open in a new tab or send to a
printer queue without exposing the user's session.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ...core.security import decode_document_token
from ...db.models import Company, Contact, Invoice, User
from ...db.session import get_session
from ...repositories.core import SettingRepository
from ...services.documents import invoice_title, render_invoice

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
