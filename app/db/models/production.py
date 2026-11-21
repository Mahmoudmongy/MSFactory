"""Production: work orders that consume materials and produce finished goods.

A work order is a small factory in the books. It names what to make and how
much, and the raw materials each unit needs. Issuing it consumes those
materials from stock and puts their cost into work-in-progress; completing it
moves that accumulated cost onto the finished good as a new FIFO layer whose
``source_type`` is ``production_receipt``.

That single marker is what makes a mixed sale tell the truth later: a good made
here carries its made cost, a good bought from a supplier carries its bought
cost, and an invoice that sells both reports each against its real origin.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantMixin, TimestampMixin

WORK_ORDER_STATUSES = ("draft", "in_progress", "completed", "cancelled")

QTY = Numeric(18, 4)
COST = Numeric(18, 6)


class WorkOrder(Base, TenantMixin, TimestampMixin):
    """One production run of a finished good."""

    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_work_orders_company_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("item_variants.id"), nullable=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY, default=1, nullable=False)
    produced_quantity: Mapped[Decimal] = mapped_column(QTY, default=0, nullable=False)

    start_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notes: Mapped[str] = mapped_column(Text, default="")

    # Frozen at completion: the cost of everything consumed, and the resulting
    # unit cost of the finished good. Copied, never derived, so repricing a
    # material later cannot restate a past production run.
    material_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(COST, default=0, nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    item = relationship("Item")
    variant = relationship("ItemVariant")
    warehouse = relationship("Warehouse")
    materials = relationship(
        "WorkOrderMaterial", back_populates="work_order", cascade="all, delete-orphan", order_by="WorkOrderMaterial.id"
    )


class WorkOrderMaterial(Base, TenantMixin):
    """A raw material the work order consumes, per unit or in total."""

    __tablename__ = "work_order_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("item_variants.id"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(QTY, default=0, nullable=False)
    # What the material actually cost when the order was issued.
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")

    work_order = relationship("WorkOrder", back_populates="materials")
    item = relationship("Item")
    variant = relationship("ItemVariant")
