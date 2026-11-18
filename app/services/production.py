"""Production service: work orders that consume materials and produce goods.

The accounting model is the same as everywhere else in the app: documents post
one balanced journal entry through the accounting service, and corrections are
reversals rather than edits.

Issuing a work order:

* consumes each material from stock (FIFO), which values it at what it truly
  cost, and
* debits work-in-progress for that material cost, crediting inventory.

Completing it:

* credits work-in-progress and debits inventory for the accumulated cost, and
* receives the finished good into stock as a ``production_receipt`` layer, so
  the item's future sale reports its cost as *manufactured* rather than bought.

Cost is frozen onto the order at completion, so repricing a material later can
never restate a past run.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..core.errors import ImmutableRecordError, ValidationError
from ..db.base import utcnow
from ..db.models import INVENTORY_CODE, WIP_CODE, WorkOrder, WorkOrderMaterial
from ..domain.money import EntryLine, to_money
from ..repositories.core import AccountRepository, AuditRepository, SequenceRepository
from ..repositories.production import WorkOrderRepository
from .accounting import AccountingService
from .inventory import InventoryService

ZERO = Decimal("0.00")


class ProductionService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.orders = WorkOrderRepository(db, company_id)
        self.accounts = AccountRepository(db, company_id)
        self.sequences = SequenceRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)
        self.accounting = AccountingService(db, company_id, user_id)
        self.inventory = InventoryService(db, company_id, user_id)

    def list(self, status: str | None = None) -> list[WorkOrder]:
        return self.orders.list(status=status)

    def get(self, work_order_id: int) -> WorkOrder:
        return self.orders.get(work_order_id)

    def create(
        self,
        *,
        item_id: int,
        quantity,
        start_date: date,
        warehouse_id: int | None = None,
        variant_id: int | None = None,
        due_date: date | None = None,
        materials: list[dict] | None = None,
        notes: str = "",
        status: str = "draft",
    ) -> WorkOrder:
        if status not in ("draft", "in_progress"):
            raise ValidationError("أمر الإنتاج يُنشأ كمسودة أو قيد التنفيذ")
        item = self.inventory.items.get(item_id)
        if not item.track_inventory:
            raise ValidationError(f"الصنف «{item.name}» غير مخزني (خدمة)")
        quantity = Decimal(str(quantity or 0))
        if quantity <= 0:
            raise ValidationError("الكمية يجب أن تكون أكبر من صفر")

        number = self.sequences.next_number("work_order", prefix="WO")
        order = WorkOrder(
            company_id=self.company_id,
            number=number,
            status="draft",
            item_id=item.id,
            variant_id=variant_id,
            warehouse_id=self.inventory.resolve_warehouse(item, warehouse_id),
            quantity=quantity,
            start_date=start_date,
            due_date=due_date,
            notes=notes,
            created_by_id=self.user_id,
        )
        for raw in materials or []:
            order.materials.append(
                WorkOrderMaterial(
                    company_id=self.company_id,
                    item_id=raw["item_id"],
                    variant_id=raw.get("variant_id"),
                    quantity=raw.get("quantity", 0),
                    notes=raw.get("notes", ""),
                )
            )
        self.orders.add(order)
        if status == "in_progress":
            self.start(order.id)
        self.audit.record(
            action="work_order.create",
            entity_type="work_order",
            entity_id=order.id,
            user_id=self.user_id,
            summary=f"أمر إنتاج {number} للصنف {item.name}",
        )
        self.db.flush()
        return order

    def start(self, work_order_id: int) -> WorkOrder:
        """Consume the materials and put their cost into work-in-progress."""
        order = self.orders.get(work_order_id)
        if order.status == "draft":
            order.status = "in_progress"
        elif order.status != "in_progress":
            raise ValidationError("لا يمكن بدء أمر في هذه الحالة")

        total = ZERO
        for material in order.materials:
            if material.quantity <= 0:
                continue
            item = self.inventory.items.get(material.item_id)
            if not item.track_inventory:
                continue
            warehouse_id = self.inventory.resolve_warehouse(item, None)
            _movement, cost = self.inventory.issue(
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=material.quantity,
                movement_date=order.start_date,
                variant_id=material.variant_id,
                kind="production_issue",
                source_type="work_order",
                source_id=order.id,
                reference=order.number,
                post_journal=False,
            )
            material.cost_amount = to_money(cost)
            total += to_money(cost)

        if total > ZERO:
            self._post(order, debit_code=WIP_CODE, credit_code=INVENTORY_CODE, amount=total, memo="صرف خامات للإنتاج")
        order.material_cost = total
        self.audit.record(
            action="work_order.start",
            entity_type="work_order",
            entity_id=order.id,
            user_id=self.user_id,
            summary=f"بدء أمر إنتاج {order.number}",
        )
        self.db.flush()
        return order

    def complete(self, work_order_id: int, *, produced_quantity=None) -> WorkOrder:
        """Move the accumulated cost onto the finished good and stock it."""
        order = self.orders.get(work_order_id)
        if order.status == "completed":
            raise ImmutableRecordError("أمر الإنتاج مكتمل بالفعل")
        if order.status not in ("in_progress", "draft"):
            raise ValidationError("لا يمكن إتمام أمر في هذه الحالة")
        if order.status == "draft":
            self.start(order.id)

        produced = Decimal(str(produced_quantity if produced_quantity is not None else order.quantity))
        if produced <= 0:
            raise ValidationError("الكمية المنتجة يجب أن تكون أكبر من صفر")

        total = to_money(order.material_cost)
        unit_cost = (total / produced) if produced else ZERO

        self.inventory.receive(
            item_id=order.item_id,
            warehouse_id=order.warehouse_id,
            quantity=produced,
            unit_cost=unit_cost,
            movement_date=order.due_date or order.start_date,
            variant_id=order.variant_id,
            kind="production_receipt",
            source_type="production_receipt",
            source_id=order.id,
            reference=order.number,
            post_journal=False,
        )
        if total > ZERO:
            self._post(order, debit_code=INVENTORY_CODE, credit_code=WIP_CODE, amount=total, memo="استلام إنتاج تام")

        order.produced_quantity = produced
        order.unit_cost = unit_cost
        order.status = "completed"
        order.completed_at = utcnow()
        self.audit.record(
            action="work_order.complete",
            entity_type="work_order",
            entity_id=order.id,
            user_id=self.user_id,
            summary=f"إتمام أمر إنتاج {order.number}",
        )
        self.db.flush()
        return order

    def cancel(self, work_order_id: int, reason: str = "") -> WorkOrder:
        """Cancel a work order, returning any consumed materials to stock."""
        order = self.orders.get(work_order_id)
        if order.status == "completed":
            raise ImmutableRecordError("لا يمكن إلغاء أمر مكتمل")
        if order.status == "cancelled":
            raise ImmutableRecordError("أمر الإنتاج ملغى بالفعل")

        total = ZERO
        for material in order.materials:
            if material.quantity <= 0 or material.cost_amount <= ZERO:
                continue
            item = self.inventory.items.get(material.item_id)
            if not item.track_inventory:
                continue
            warehouse_id = self.inventory.resolve_warehouse(item, None)
            self.inventory.receive(
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=material.quantity,
                unit_cost=material.cost_amount / material.quantity,
                movement_date=order.start_date,
                variant_id=material.variant_id,
                kind="adjustment_in",
                source_type="work_order_cancel",
                source_id=order.id,
                reference=order.number,
                notes=reason,
                post_journal=False,
            )
            total += to_money(material.cost_amount)

        if total > ZERO:
            self._post(order, debit_code=INVENTORY_CODE, credit_code=WIP_CODE, amount=total, memo="إلغاء أمر إنتاج")
        order.status = "cancelled"
        self.audit.record(
            action="work_order.cancel",
            entity_type="work_order",
            entity_id=order.id,
            user_id=self.user_id,
            summary=f"إلغاء أمر إنتاج {order.number}: {reason}",
        )
        self.db.flush()
        return order

    def _post(self, order: WorkOrder, *, debit_code: str, credit_code: str, amount, memo: str) -> None:
        debit = self.accounts.by_code(debit_code)
        credit = self.accounts.by_code(credit_code)
        if not debit or not credit:
            return
        self.accounting.post(
            entry_date=order.due_date or order.start_date,
            lines=[
                EntryLine(account_id=debit.id, debit=to_money(amount), description=memo),
                EntryLine(account_id=credit.id, credit=to_money(amount), description=memo),
            ],
            memo=f"{memo} — {order.number}",
            reference=order.number,
            source_type="work_order",
            source_id=order.id,
        )
