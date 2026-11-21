"""Payment endpoints."""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...core.security import DOCUMENT_TOKEN_MINUTES, create_document_token
from ...db.session import get_session
from ...repositories.core import SettingRepository
from ...services.payments import PaymentService
from ...services.reports.excel import build_workbook, suggested_filename
from ..v1.deps import Principal, require
from ..v1.idempotency import replay_or_execute
from ..v1.schemas import PaymentCreate, PaymentOut

router = APIRouter(prefix="/payments", tags=["payments"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("", response_model=list[PaymentOut])
def list_payments(
    kind: str | None = None,
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.PAYMENT_VIEW)),
    db: Session = Depends(get_session),
):
    return PaymentService(db, principal.company_id, principal.user.id).list(kind=kind, start=start, end=end)


@router.get("/export.xlsx")
def export_payments(
    kind: str | None = None,
    start: date | None = None,
    end: date | None = None,
    principal: Principal = Depends(require(Permission.PAYMENT_VIEW)),
    db: Session = Depends(get_session),
):
    """Download the vouchers as a workbook, with the same filters as the screen."""
    payments = PaymentService(db, principal.company_id, principal.user.id).list(
        kind=kind, start=start, end=end
    )
    company_name = SettingRepository(db, principal.company_id).get("company_name", "")
    rows = [
        {
            "number": payment.number,
            "date": payment.date,
            "kind": "تحصيل" if payment.kind == "receive" else "صرف",
            "contact": payment.contact.name if payment.contact else "",
            "amount": payment.amount,
            "reference": payment.reference or "",
        }
        for payment in payments
    ]
    payload = {"company": company_name, "count": len(rows), "payments": rows}
    return Response(
        content=build_workbook("payments", payload, title="السندات"),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(suggested_filename('payments', company_name))}"
            )
        },
    )


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_payment(
    payload: PaymentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require(Permission.PAYMENT_MANAGE)),
    db: Session = Depends(get_session),
):
    def run() -> PaymentOut:
        payment = PaymentService(db, principal.company_id, principal.user.id).create(
            kind=payload.kind,
            contact_id=payload.contact_id,
            payment_date=payload.date,
            amount=payload.amount,
            bank_account_id=payload.bank_account_id,
            reference=payload.reference,
            invoice_id=payload.invoice_id,
        )
        db.commit()
        return PaymentOut.model_validate(payment)

    return replay_or_execute(db, principal.company_id, idempotency_key, "/payments", run, PaymentOut)


@router.post("/{payment_id}/print-token")
def payment_print_token(
    payment_id: int,
    principal: Principal = Depends(require(Permission.PAYMENT_VIEW)),
    db: Session = Depends(get_session),
):
    """Mint a one-document link for a receipt or payment voucher.

    Same reasoning as the invoice link: the access token stays out of the URL,
    and the returned token opens this one voucher and expires in minutes.
    """
    service = PaymentService(db, principal.company_id, principal.user.id)
    service.payments.get(payment_id)
    token = create_document_token(principal.user.id, principal.company_id, "payment", payment_id)
    return {
        "path": f"/documents/payment/{payment_id}?t={token}",
        "expires_in": DOCUMENT_TOKEN_MINUTES * 60,
    }


@router.post("/{payment_id}/reverse", status_code=status.HTTP_204_NO_CONTENT)
def reverse_payment(
    payment_id: int,
    principal: Principal = Depends(require(Permission.PAYMENT_MANAGE)),
    db: Session = Depends(get_session),
):
    PaymentService(db, principal.company_id, principal.user.id).reverse(payment_id)
    db.commit()
