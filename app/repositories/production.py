"""Repository for work orders (production)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..core.errors import NotFoundError
from ..db.models import WorkOrder


class WorkOrderRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(self, status: str | None = None) -> list[WorkOrder]:
        stmt = (
            select(WorkOrder)
            .options(selectinload(WorkOrder.materials))
            .where(WorkOrder.company_id == self.company_id)
            .order_by(WorkOrder.id.desc())
        )
        if status:
            stmt = stmt.where(WorkOrder.status == status)
        return list(self.db.scalars(stmt))

    def get(self, work_order_id: int) -> WorkOrder:
        order = self.db.scalar(
            select(WorkOrder)
            .options(selectinload(WorkOrder.materials))
            .where(WorkOrder.company_id == self.company_id, WorkOrder.id == work_order_id)
        )
        if not order:
            raise NotFoundError("أمر الإنتاج غير موجود")
        return order

    def add(self, order: WorkOrder) -> WorkOrder:
        order.company_id = self.company_id
        self.db.add(order)
        self.db.flush()
        return order
