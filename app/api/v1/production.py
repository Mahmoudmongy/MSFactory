"""Production endpoints: work orders.

Reads need ``PRODUCTION_VIEW``; creating, starting, completing and cancelling
need ``PRODUCTION_MANAGE``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...core.security import DOCUMENT_TOKEN_MINUTES, create_document_token
from ...db.session import get_session
from ...services.production import ProductionService
from ..v1.deps import Principal, require
from ..v1.schemas import WorkOrderCancel, WorkOrderComplete, WorkOrderCreate, WorkOrderOut

router = APIRouter(prefix="/production", tags=["production"])


def _service(principal: Principal, db: Session) -> ProductionService:
    return ProductionService(db, principal.company_id, principal.user.id)


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    status_filter: str | None = None,
    principal: Principal = Depends(require(Permission.PRODUCTION_VIEW)),
    db: Session = Depends(get_session),
):
    return _service(principal, db).list(status=status_filter)


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderOut)
def get_work_order(
    work_order_id: int,
    principal: Principal = Depends(require(Permission.PRODUCTION_VIEW)),
    db: Session = Depends(get_session),
):
    return _service(principal, db).get(work_order_id)


@router.post("/work-orders/{work_order_id}/print-token")
def work_order_print_token(
    work_order_id: int,
    principal: Principal = Depends(require(Permission.PRODUCTION_VIEW)),
    db: Session = Depends(get_session),
):
    """Mint a one-document link for the shop-floor copy of a work order.

    Same reasoning as the invoice and voucher links: the access token stays out
    of the URL, and the returned token opens this one order and expires in
    minutes.
    """
    _service(principal, db).get(work_order_id)
    token = create_document_token(
        principal.user.id, principal.company_id, "work_order", work_order_id
    )
    return {
        "path": f"/documents/work-order/{work_order_id}?t={token}",
        "expires_in": DOCUMENT_TOKEN_MINUTES * 60,
    }


@router.post("/work-orders", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def create_work_order(
    payload: WorkOrderCreate,
    principal: Principal = Depends(require(Permission.PRODUCTION_MANAGE)),
    db: Session = Depends(get_session),
):
    order = _service(principal, db).create(
        item_id=payload.item_id,
        quantity=payload.quantity,
        start_date=payload.start_date,
        warehouse_id=payload.warehouse_id,
        variant_id=payload.variant_id,
        due_date=payload.due_date,
        materials=[m.model_dump() for m in payload.materials],
        notes=payload.notes,
        status=payload.status,
    )
    db.commit()
    return WorkOrderOut.model_validate(order)


@router.post("/work-orders/{work_order_id}/start", response_model=WorkOrderOut)
def start_work_order(
    work_order_id: int,
    principal: Principal = Depends(require(Permission.PRODUCTION_MANAGE)),
    db: Session = Depends(get_session),
):
    order = _service(principal, db).start(work_order_id)
    db.commit()
    return WorkOrderOut.model_validate(order)


@router.post("/work-orders/{work_order_id}/complete", response_model=WorkOrderOut)
def complete_work_order(
    work_order_id: int,
    payload: WorkOrderComplete,
    principal: Principal = Depends(require(Permission.PRODUCTION_MANAGE)),
    db: Session = Depends(get_session),
):
    order = _service(principal, db).complete(work_order_id, produced_quantity=payload.produced_quantity)
    db.commit()
    return WorkOrderOut.model_validate(order)


@router.post("/work-orders/{work_order_id}/cancel", response_model=WorkOrderOut)
def cancel_work_order(
    work_order_id: int,
    payload: WorkOrderCancel,
    principal: Principal = Depends(require(Permission.PRODUCTION_MANAGE)),
    db: Session = Depends(get_session),
):
    order = _service(principal, db).cancel(work_order_id, reason=payload.reason)
    db.commit()
    return WorkOrderOut.model_validate(order)
