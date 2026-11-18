"""Payment endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.session import get_session
from ...services.payments import PaymentService
from ..v1.deps import Principal, require
from ..v1.idempotency import replay_or_execute
from ..v1.schemas import PaymentCreate, PaymentOut

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("", response_model=list[PaymentOut])
def list_payments(
    kind: str | None = None,
    principal: Principal = Depends(require(Permission.PAYMENT_VIEW)),
    db: Session = Depends(get_session),
):
    return PaymentService(db, principal.company_id, principal.user.id).list(kind=kind)


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


@router.post("/{payment_id}/reverse", status_code=status.HTTP_204_NO_CONTENT)
def reverse_payment(
    payment_id: int,
    principal: Principal = Depends(require(Permission.PAYMENT_MANAGE)),
    db: Session = Depends(get_session),
):
    PaymentService(db, principal.company_id, principal.user.id).reverse(payment_id)
    db.commit()
