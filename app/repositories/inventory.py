"""Tenant-scoped inventory repositories, including the FIFO engine.

``StockLayerRepository.consume`` is the only code that reduces a layer, so FIFO
ordering is enforced in exactly one place and cannot drift between callers.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from ..core.errors import ConflictError, NotFoundError
from ..db.models import (
    Item,
    ItemVariant,
    StockLayer,
    StockMovement,
    UnitOfMeasure,
    Warehouse,
)

ZERO = Decimal("0.00")
FOUR = Decimal("0.0001")


def scoped(stmt: Select, model, company_id: int) -> Select:
    return stmt.where(model.company_id == company_id)


class UnitRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(self, include_inactive: bool = False) -> list[UnitOfMeasure]:
        stmt = scoped(select(UnitOfMeasure), UnitOfMeasure, self.company_id).order_by(UnitOfMeasure.code)
        if not include_inactive:
            stmt = stmt.where(UnitOfMeasure.is_active.is_(True))
        return list(self.db.scalars(stmt))

    def get(self, unit_id: int) -> UnitOfMeasure:
        unit = self.db.scalar(
            scoped(select(UnitOfMeasure).where(UnitOfMeasure.id == unit_id), UnitOfMeasure, self.company_id)
        )
        if not unit:
            raise NotFoundError("Unit not found")
        return unit

    def by_code(self, code: str) -> UnitOfMeasure | None:
        return self.db.scalar(
            scoped(select(UnitOfMeasure).where(UnitOfMeasure.code == code), UnitOfMeasure, self.company_id)
        )

    def add(self, unit: UnitOfMeasure) -> UnitOfMeasure:
        unit.company_id = self.company_id
        self.db.add(unit)
        self.db.flush()
        return unit


class WarehouseRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(self, include_inactive: bool = False) -> list[Warehouse]:
        stmt = scoped(select(Warehouse), Warehouse, self.company_id).order_by(Warehouse.code)
        if not include_inactive:
            stmt = stmt.where(Warehouse.is_active.is_(True))
        return list(self.db.scalars(stmt))

    def get(self, warehouse_id: int) -> Warehouse:
        warehouse = self.db.scalar(
            scoped(select(Warehouse).where(Warehouse.id == warehouse_id), Warehouse, self.company_id)
        )
        if not warehouse:
            raise NotFoundError("Warehouse not found")
        return warehouse

    def by_code(self, code: str) -> Warehouse | None:
        return self.db.scalar(
            scoped(select(Warehouse).where(Warehouse.code == code), Warehouse, self.company_id)
        )

    def add(self, warehouse: Warehouse) -> Warehouse:
        warehouse.company_id = self.company_id
        self.db.add(warehouse)
        self.db.flush()
        return warehouse

    def children_of(self, warehouse_id: int) -> list[Warehouse]:
        """Direct children only — used to block deleting a parent node."""
        stmt = scoped(
            select(Warehouse).where(Warehouse.parent_id == warehouse_id), Warehouse, self.company_id
        )
        return list(self.db.scalars(stmt))

    def descendant_ids(self, warehouse_id: int) -> list[int]:
        """This warehouse plus every warehouse beneath it, at any depth."""
        children: dict[int | None, list[int]] = {}
        for row_id, parent_id in self.db.execute(
            select(Warehouse.id, Warehouse.parent_id).where(Warehouse.company_id == self.company_id)
        ):
            children.setdefault(parent_id, []).append(row_id)

        collected: list[int] = []
        stack = [warehouse_id]
        while stack:
            current = stack.pop()
            collected.append(current)
            stack.extend(children.get(current, []))
        return collected


class ItemRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(
        self,
        *,
        search: str | None = None,
        item_type: str | None = None,
        group_id: int | None = None,
        include_inactive: bool = False,
    ) -> list[Item]:
        stmt = (
            scoped(select(Item), Item, self.company_id)
            .options(selectinload(Item.variants))
            .order_by(Item.code)
        )
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(Item.name.ilike(like) | Item.code.ilike(like) | Item.name_ar.ilike(like))
        if item_type:
            stmt = stmt.where(Item.item_type == item_type)
        if group_id:
            stmt = stmt.where(Item.item_group_id == group_id)
        if not include_inactive:
            stmt = stmt.where(Item.is_active.is_(True))
        return list(self.db.scalars(stmt))

    def get(self, item_id: int) -> Item:
        item = self.db.scalar(
            scoped(select(Item).where(Item.id == item_id), Item, self.company_id).options(
                selectinload(Item.variants)
            )
        )
        if not item:
            raise NotFoundError("Item not found")
        return item

    def by_code(self, code: str) -> Item | None:
        return self.db.scalar(scoped(select(Item).where(Item.code == code), Item, self.company_id))

    def add(self, item: Item) -> Item:
        item.company_id = self.company_id
        self.db.add(item)
        self.db.flush()
        return item

    def variants(self, item_id: int) -> list[ItemVariant]:
        return list(
            self.db.scalars(
                select(ItemVariant)
                .where(ItemVariant.company_id == self.company_id, ItemVariant.item_id == item_id)
                .order_by(ItemVariant.id)
            )
        )

    def variant(self, variant_id: int) -> ItemVariant:
        variant = self.db.scalar(
            scoped(select(ItemVariant).where(ItemVariant.id == variant_id), ItemVariant, self.company_id)
        )
        if not variant:
            raise NotFoundError("Item variant not found")
        return variant


class StockMovementRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def add(self, movement: StockMovement) -> StockMovement:
        movement.company_id = self.company_id
        self.db.add(movement)
        self.db.flush()
        return movement

    def get(self, movement_id: int) -> StockMovement:
        movement = self.db.scalar(
            scoped(select(StockMovement).where(StockMovement.id == movement_id), StockMovement, self.company_id)
        )
        if not movement:
            raise NotFoundError("Stock movement not found")
        return movement

    def for_item(self, item_id: int, variant_id: int | None = None) -> list[StockMovement]:
        stmt = (
            scoped(select(StockMovement), StockMovement, self.company_id)
            .where(StockMovement.item_id == item_id)
            .order_by(StockMovement.movement_date, StockMovement.id)
        )
        if variant_id is not None:
            stmt = stmt.where(StockMovement.variant_id == variant_id)
        return list(self.db.scalars(stmt))

    def query(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        item_id: int | None = None,
        warehouse_id: int | None = None,
        kind: str | None = None,
        limit: int = 500,
    ) -> list[StockMovement]:
        stmt = scoped(select(StockMovement), StockMovement, self.company_id)
        if start:
            stmt = stmt.where(StockMovement.movement_date >= start)
        if end:
            stmt = stmt.where(StockMovement.movement_date <= end)
        if item_id:
            stmt = stmt.where(StockMovement.item_id == item_id)
        if warehouse_id:
            stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
        if kind:
            stmt = stmt.where(StockMovement.kind == kind)
        stmt = stmt.order_by(StockMovement.movement_date.desc(), StockMovement.id.desc()).limit(limit)
        return list(self.db.scalars(stmt))


class StockLayerRepository:
    """The FIFO engine: layers are created on receipt and consumed oldest-first."""

    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def quantity_on_hand(self, item_id: int, variant_id: int | None = None) -> Decimal:
        stmt = select(func.coalesce(func.sum(StockLayer.quantity_remaining), 0)).where(
            StockLayer.company_id == self.company_id, StockLayer.item_id == item_id
        )
        if variant_id is not None:
            stmt = stmt.where(StockLayer.variant_id == variant_id)
        return Decimal(str(self.db.scalar(stmt) or 0)).quantize(FOUR)

    def quantity_in_warehouse(
        self, item_id: int, warehouse_id: int, variant_id: int | None = None
    ) -> Decimal:
        """Quantity of an item sitting in one warehouse.

        Layers are company-wide per item (a transfer must not revalue), so the
        per-warehouse figure has to come from the movement ledger instead.
        """
        stmt = select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.company_id == self.company_id,
            StockMovement.item_id == item_id,
            StockMovement.warehouse_id == warehouse_id,
        )
        if variant_id is not None:
            stmt = stmt.where(StockMovement.variant_id == variant_id)
        return Decimal(str(self.db.scalar(stmt) or 0)).quantize(FOUR)

    def value_on_hand(self, *, item_id: int | None = None, warehouse_id: int | None = None) -> Decimal:
        stmt = select(
            func.coalesce(func.sum(StockLayer.quantity_remaining * StockLayer.unit_cost), 0)
        ).where(StockLayer.company_id == self.company_id)
        if item_id is not None:
            stmt = stmt.where(StockLayer.item_id == item_id)
        if warehouse_id is not None:
            stmt = stmt.where(StockLayer.warehouse_id == warehouse_id)
        return Decimal(str(self.db.scalar(stmt) or 0))

    def remaining_layers(self, item_id: int, variant_id: int | None = None) -> list[StockLayer]:
        """Layers that still hold quantity, oldest first."""
        stmt = (
            select(StockLayer)
            .where(
                StockLayer.company_id == self.company_id,
                StockLayer.item_id == item_id,
                StockLayer.quantity_remaining > 0,
            )
            .order_by(StockLayer.received_at, StockLayer.id)
        )
        if variant_id is not None:
            stmt = stmt.where(StockLayer.variant_id == variant_id)
        return list(self.db.scalars(stmt))

    def latest_unit_cost(self, item_id: int, variant_id: int | None = None) -> Decimal | None:
        stmt = (
            select(StockLayer.unit_cost)
            .where(StockLayer.company_id == self.company_id, StockLayer.item_id == item_id)
            .order_by(StockLayer.received_at.desc(), StockLayer.id.desc())
            .limit(1)
        )
        if variant_id is not None:
            stmt = stmt.where(StockLayer.variant_id == variant_id)
        value = self.db.scalar(stmt)
        return Decimal(str(value)) if value is not None else None

    def consume(
        self,
        item_id: int,
        variant_id: int | None,
        quantity: Decimal,
        method: str = "fifo",
    ) -> tuple[list[tuple[StockLayer, Decimal, Decimal]], Decimal]:
        """Draw ``quantity`` from the pool, priced by the company's costing method.

        ``fifo`` consumes the oldest layer first and charges that layer's own
        cost, so a cost of sale names the purchase batch it came from.

        ``weighted_average`` charges the pool's average cost and then revalues
        every remaining layer to that same average. Revaluing is what keeps
        ``sum(quantity_remaining * unit_cost)`` equal to the true inventory
        value; without it, drawing from differently-priced layers would leave
        the pool misstated.

        Rows are locked so two concurrent issues cannot consume the same slice
        twice. Returns ``[(layer, taken, unit_cost)]`` and the total cost.
        """
        stmt = (
            select(StockLayer)
            .where(
                StockLayer.company_id == self.company_id,
                StockLayer.item_id == item_id,
                StockLayer.quantity_remaining > 0,
            )
            .order_by(StockLayer.received_at, StockLayer.id)
            .with_for_update()
        )
        if variant_id is not None:
            stmt = stmt.where(StockLayer.variant_id == variant_id)

        layers = list(self.db.scalars(stmt))
        total_qty = sum((Decimal(str(layer.quantity_remaining)) for layer in layers), ZERO)
        requested = Decimal(str(quantity))
        if requested > total_qty:
            raise ConflictError(
                "الرصيد المتاح لا يكفي لإتمام الصرف",
                details={"available": str(total_qty.quantize(FOUR)), "requested": str(requested.quantize(FOUR))},
            )

        average = None
        if method == "weighted_average" and total_qty > 0:
            total_value = sum(
                (Decimal(str(layer.quantity_remaining)) * Decimal(str(layer.unit_cost)) for layer in layers),
                ZERO,
            )
            average = total_value / total_qty

        remaining = requested
        taken_rows: list[tuple[StockLayer, Decimal, Decimal]] = []
        total_cost = ZERO

        for layer in layers:
            if remaining <= 0:
                break
            available = Decimal(str(layer.quantity_remaining))
            take = available if available <= remaining else remaining
            layer.quantity_remaining = (available - take).quantize(FOUR)
            unit_cost = average if average is not None else Decimal(str(layer.unit_cost))
            taken_rows.append((layer, take.quantize(FOUR), unit_cost))
            total_cost += take * unit_cost
            remaining -= take

        if average is not None:
            # Revalue what is left so the pool stays at the average cost.
            for layer in layers:
                if layer.quantity_remaining > 0:
                    layer.unit_cost = average.quantize(Decimal("0.000001"))

        self.db.flush()
        return taken_rows, Decimal(str(total_cost)).quantize(Decimal("0.01"))

    def on_hand_rows(
        self, *, warehouse_id: int | None = None, item_id: int | None = None
    ) -> list[dict]:
        """Stock on hand per item and variant, valued at FIFO cost.

        Quantity per warehouse comes from movements (a transfer moves quantity
        without touching layers); value is company-wide per item, so a
        per-warehouse value is prorated by that warehouse's share of quantity.
        """
        qty_stmt = (
            select(
                StockMovement.item_id,
                StockMovement.variant_id,
                StockMovement.warehouse_id,
                func.coalesce(func.sum(StockMovement.quantity), 0),
            )
            .where(StockMovement.company_id == self.company_id)
            .group_by(StockMovement.item_id, StockMovement.variant_id, StockMovement.warehouse_id)
        )
        if warehouse_id is not None:
            qty_stmt = qty_stmt.where(StockMovement.warehouse_id == warehouse_id)
        if item_id is not None:
            qty_stmt = qty_stmt.where(StockMovement.item_id == item_id)

        qty_by_key: dict[tuple[int, int | None], Decimal] = {}
        qty_by_wh: dict[tuple[int, int | None, int], Decimal] = {}
        for i_id, v_id, w_id, qty in self.db.execute(qty_stmt):
            qty = Decimal(str(qty)).quantize(FOUR)
            if qty == 0:
                continue
            key = (i_id, v_id)
            qty_by_key[key] = qty_by_key.get(key, ZERO) + qty
            qty_by_wh[(i_id, v_id, w_id)] = qty

        value_by_key: dict[tuple[int, int | None], Decimal] = {}
        val_stmt = (
            select(
                StockLayer.item_id,
                StockLayer.variant_id,
                func.coalesce(func.sum(StockLayer.quantity_remaining * StockLayer.unit_cost), 0),
            )
            .where(StockLayer.company_id == self.company_id)
            .group_by(StockLayer.item_id, StockLayer.variant_id)
        )
        if item_id is not None:
            val_stmt = val_stmt.where(StockLayer.item_id == item_id)
        for i_id, v_id, value in self.db.execute(val_stmt):
            value_by_key[(i_id, v_id)] = Decimal(str(value))

        item_cache: dict[int, Item] = {}
        warehouse_cache: dict[int, Warehouse] = {}
        variant_cache: dict[int, ItemVariant] = {}
        rows: list[dict] = []
        for (i_id, v_id), qty in qty_by_key.items():
            if qty == 0:
                continue
            item = item_cache.get(i_id) or self.db.get(Item, i_id)
            if item is None:
                continue
            item_cache[i_id] = item
            variant = None
            if v_id:
                variant = variant_cache.get(v_id) or self.db.get(ItemVariant, v_id)
                variant_cache[v_id] = variant
            total_value = value_by_key.get((i_id, v_id), ZERO)
            unit_cost = (total_value / qty) if qty else ZERO

            if warehouse_id is not None:
                rows.append(
                    self._row(item, variant, warehouse_id, qty, total_value, unit_cost, warehouse_cache)
                )
                continue
            for (r_i, r_v, w_id), wh_qty in qty_by_wh.items():
                if r_i != i_id or r_v != v_id:
                    continue
                share = (total_value * wh_qty / qty) if qty else ZERO
                rows.append(
                    self._row(item, variant, w_id, wh_qty, share, unit_cost, warehouse_cache)
                )

        rows.sort(key=lambda r: (r["item_code"], r["variant_code"] or ""))
        return rows

    def _row(self, item, variant, warehouse_id, qty, value, unit_cost, warehouse_cache) -> dict:
        warehouse = warehouse_cache.get(warehouse_id)
        if warehouse is None:
            warehouse = self.db.get(Warehouse, warehouse_id)
            warehouse_cache[warehouse_id] = warehouse
        return {
            "item_id": item.id,
            "item_code": item.code,
            "item_name": item.name,
            "item_name_ar": item.name_ar,
            "variant_id": variant.id if variant else None,
            "variant_code": variant.code if variant else "",
            "colour": variant.colour if variant else "",
            "size": variant.size if variant else "",
            "unit": item.unit.name_ar or item.unit.name if item.unit else "",
            "warehouse_id": warehouse_id,
            "warehouse_name": warehouse.name if warehouse else "",
            "quantity": qty,
            "unit_cost": Decimal(str(unit_cost)).quantize(Decimal("0.000001")),
            "value": Decimal(str(value)).quantize(Decimal("0.01")),
            "reorder_level": item.reorder_level,
        }
