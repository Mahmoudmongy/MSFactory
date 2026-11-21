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
from ...db.models import Account, Company, Contact, Invoice, Item, Payment, User, Warehouse, WorkOrder
from ...db.session import get_session
from ...repositories.core import SettingRepository
from ...services.barcode import is_encodable
from ...services.documents import (
    invoice_title,
    payment_title,
    render_barcode_labels,
    render_invoice,
    render_payment,
    render_work_order,
    work_order_title,
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


@router.get("/work-order/{work_order_id}", response_class=HTMLResponse)
def print_work_order(
    work_order_id: int,
    t: str = Query(..., description="Short-lived document token"),
    db: Session = Depends(get_session),
):
    """The shop-floor copy of a production order."""
    payload = decode_document_token(t, "work_order", work_order_id)
    if payload is None:
        raise _unauthorised()

    company_id = int(payload["cid"])
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorised()

    order = db.get(WorkOrder, work_order_id)
    if order is None or order.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "أمر الإنتاج غير موجود"},
        )

    company = db.get(Company, company_id)
    settings = SettingRepository(db, company_id).all()

    # Resolve the names in one query per lookup rather than walking the
    # relationships: a lazy load here would be free to cross into another
    # company's rows if the model ever gained a backref.
    item = db.get(Item, order.item_id)
    warehouse = db.get(Warehouse, order.warehouse_id)
    material_ids = [m.item_id for m in order.materials]
    material_names: dict[int, str] = {}
    if material_ids:
        rows = db.scalars(
            select(Item).where(Item.company_id == company_id, Item.id.in_(material_ids))
        )
        material_names = {row.id: (row.name_ar or row.name) for row in rows}

    html = render_work_order(
        order,
        company,
        settings,
        item_name=(item.name_ar or item.name) if item else "",
        warehouse_name=(warehouse.name_ar or warehouse.name) if warehouse else "",
        material_names=material_names,
    )
    headers = dict(NO_STORE)
    headers["Content-Disposition"] = (
        f"inline; filename=\"work-order-{order.id}.html\"; "
        f"filename*=UTF-8''{quote(work_order_title(order) + '.html')}"
    )
    return HTMLResponse(html, headers=headers)


# A label sheet is the one printable document that is not a single row: the
# user picks a batch of items, and the batch is too long to carry in a token.
# The token therefore names the *sheet* (id 0) and the company, and the ids
# travel in the query string. That is safe because the route re-checks every id
# against the token's company, and the sheet shows nothing the caller could not
# already read through ``STOCK_VIEW``.
LABELS_DOCUMENT_ID = 0
MAX_LABELS = 200


@router.get("/item-labels", response_class=HTMLResponse)
def print_item_labels(
    t: str = Query(..., description="Short-lived document token"),
    ids: str = Query("", description="Comma-separated item ids; empty means all"),
    db: Session = Depends(get_session),
):
    """A sheet of scannable labels for the chosen items."""
    payload = decode_document_token(t, "item_labels", LABELS_DOCUMENT_ID)
    if payload is None:
        raise _unauthorised()

    company_id = int(payload["cid"])
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorised()

    query = select(Item).where(Item.company_id == company_id, Item.barcode != "")
    requested = [part.strip() for part in ids.split(",") if part.strip().isdigit()]
    if requested:
        query = query.where(Item.id.in_([int(part) for part in requested[:MAX_LABELS]]))
    items = list(db.scalars(query.order_by(Item.id).limit(MAX_LABELS)))

    labels = [
        {
            "barcode": item.barcode,
            "name": item.name_ar or item.name,
            "meta": item.code,
        }
        for item in items
        # An item can hold any text in ``barcode``; a character Code 128 cannot
        # encode would print a bar pattern that scans to something else, so
        # those rows are skipped rather than printed wrong.
        if is_encodable(item.barcode or "")
    ]

    company = db.get(Company, company_id)
    settings = SettingRepository(db, company_id).all()
    html = render_barcode_labels(labels, company, settings)
    headers = dict(NO_STORE)
    headers["Content-Disposition"] = (
        "inline; filename=\"item-labels.html\"; "
        f"filename*=UTF-8''{quote('ملصقات-باركود.html')}"
    )
    return HTMLResponse(html, headers=headers)
