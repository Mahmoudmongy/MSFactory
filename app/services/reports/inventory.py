"""Inventory reports: valuation, movement, aging and reorder.

A factory lives on these. Valuation says what the stock is worth and on which
costing basis; movement says what came in and went out; aging says what has sat
too long; reorder says what to buy or make next. All of them read the stock
ledger, so a valuation always ties to the movements that produced it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from ...db.models import Item, ItemVariant, StockLayer, StockMovement
from .base import ZERO, money

#: Stock older than this many days is reported as slow-moving.
SLOW_MOVING_DAYS = 90
#: Stock older than this many days is reported as dead.
DEAD_STOCK_DAYS = 180


class InventoryMixin:
    """Stock valuation, movement, aging, reorder and profitability."""

    def stock_valuation(self, *, warehouse_id: int | None = None, as_of: date | None = None) -> dict:
        """What the stock is worth, line by line, at cost.

        Built from remaining FIFO layers rather than a stored value, so the
        total is always the sum of the batches still on hand and each line can
        be traced to the receipt that created it.
        """
        stmt = (
            select(
                StockLayer.item_id,
                StockLayer.variant_id,
                StockLayer.warehouse_id,
                func.sum(StockLayer.quantity_remaining),
                func.sum(StockLayer.quantity_remaining * StockLayer.unit_cost),
            )
            .where(
                StockLayer.company_id == self.company_id,
                StockLayer.quantity_remaining > 0,
            )
            .group_by(StockLayer.item_id, StockLayer.variant_id, StockLayer.warehouse_id)
        )
        if warehouse_id:
            stmt = stmt.where(StockLayer.warehouse_id.in_(self.warehouses.descendant_ids(warehouse_id)))
        if as_of:
            stmt = stmt.where(StockLayer.received_at <= as_of)

        warehouse_names = {w.id: (w.name_ar or w.name) for w in self.warehouses.list(include_inactive=True)}
        variant_cache: dict[int, ItemVariant] = {}
        item_cache: dict[int, Item] = {}
        lines = []
        total_value = ZERO
        total_quantity = Decimal("0")

        for item_id, variant_id, wh_id, quantity, value in self.db.execute(stmt):
            quantity = Decimal(str(quantity or 0))
            value = money(value)
            if quantity == 0:
                continue
            item = item_cache.get(item_id) or self.items.get(item_id)
            item_cache[item_id] = item
            variant = None
            if variant_id and variant_id not in variant_cache:
                variant_cache[variant_id] = self.items.variant(variant_id)
            variant = variant_cache.get(variant_id)
            total_value += value
            total_quantity += quantity
            lines.append(
                {
                    "item_id": item_id,
                    "item_code": item.code,
                    "item_name": item.name,
                    "item_name_ar": item.name_ar,
                    "variant_id": variant_id,
                    "variant_label": variant.label if variant else "",
                    "warehouse_id": wh_id,
                    "warehouse_name": warehouse_names.get(wh_id, ""),
                    "quantity": quantity,
                    "value": value,
                    "unit_cost": (value / quantity).quantize(Decimal("0.0001")) if quantity else ZERO,
                }
            )

        lines.sort(key=lambda r: r["value"], reverse=True)
        return {
            "lines": lines,
            "total_value": total_value,
            "total_quantity": total_quantity,
            "line_count": len(lines),
            "costing_method": self._costing_method(),
        }

    def _costing_method(self) -> str:
        from ...repositories.core import SettingRepository

        return SettingRepository(self.db, self.company_id).get("costing_method", "fifo")

    def stock_movement_report(self, start: date | None = None, end: date | None = None) -> dict:
        """What came in, what went out and what that did to the balance.

        Grouped by item and movement kind so a storekeeper can see receipts,
        issues, transfers and write-offs side by side and reconcile them to the
        closing quantity.
        """
        stmt = (
            select(
                StockMovement.item_id,
                StockMovement.kind,
                func.sum(StockMovement.quantity),
                func.sum(StockMovement.total_cost),
            )
            .where(StockMovement.company_id == self.company_id)
            .group_by(StockMovement.item_id, StockMovement.kind)
        )
        if start:
            stmt = stmt.where(StockMovement.movement_date >= start)
        if end:
            stmt = stmt.where(StockMovement.movement_date <= end)

        per_item: dict[int, dict] = {}
        for item_id, kind, quantity, cost in self.db.execute(stmt):
            row = per_item.setdefault(
                item_id,
                {"item_id": item_id, "in": {}, "out": {}, "net_quantity": Decimal("0"), "net_value": ZERO},
            )
            quantity = Decimal(str(quantity or 0))
            cost = money(cost)
            row["net_quantity"] += quantity
            row["net_value"] += cost
            bucket = "in" if quantity > 0 else "out"
            row[bucket][kind] = row[bucket].get(kind, Decimal("0")) + abs(quantity)

        items = []
        for item_id, row in per_item.items():
            item = self.items.get(item_id)
            row["item_code"] = item.code
            row["item_name"] = item.name
            row["item_name_ar"] = item.name_ar
            items.append(row)
        items.sort(key=lambda r: r["item_code"])

        return {
            "items": items,
            "total_in": sum((sum(r["in"].values(), Decimal("0")) for r in items), Decimal("0")),
            "total_out": sum((sum(r["out"].values(), Decimal("0")) for r in items), Decimal("0")),
        }

    def stock_aging(self, as_of: date | None = None) -> dict:
        """How long the stock on hand has been sitting, in age bands.

        Age is measured from the receipt that created each layer, so it answers
        "what has been here too long" rather than "when did I last touch it".
        """
        as_of = as_of or datetime.now(UTC).date()
        rows = self.db.execute(
            select(
                StockLayer.item_id,
                StockLayer.received_at,
                StockLayer.quantity_remaining,
                StockLayer.unit_cost,
                StockLayer.warehouse_id,
            ).where(StockLayer.company_id == self.company_id, StockLayer.quantity_remaining > 0)
        ).all()

        bands = {"0_30": ZERO, "31_90": ZERO, "91_180": ZERO, "over_180": ZERO}
        slow: list[dict] = []
        for item_id, received_at, quantity, unit_cost, _warehouse_id in rows:
            quantity = Decimal(str(quantity or 0))
            value = money(quantity * Decimal(str(unit_cost or 0)))
            age = (as_of - received_at).days
            band = "0_30" if age <= 30 else "31_90" if age <= 90 else "91_180" if age <= 180 else "over_180"
            bands[band] += value
            if age >= SLOW_MOVING_DAYS:
                item = self.items.get(item_id)
                slow.append(
                    {
                        "item_id": item_id,
                        "item_code": item.code,
                        "item_name_ar": item.name_ar or item.name,
                        "received_at": received_at,
                        "age_days": age,
                        "quantity": quantity,
                        "value": value,
                        "is_dead": age >= DEAD_STOCK_DAYS,
                    }
                )
        slow.sort(key=lambda r: r["age_days"], reverse=True)
        return {
            "bands": bands,
            "total_value": sum(bands.values(), ZERO),
            "slow_moving": slow,
            "slow_moving_value": sum((r["value"] for r in slow), ZERO),
            "slow_moving_days": SLOW_MOVING_DAYS,
            "dead_stock_days": DEAD_STOCK_DAYS,
        }

    def reorder_report(self) -> dict:
        """Items at or below their reorder level, with what is on hand.

        This is the buying and production worklist: a factory checks it before
        raising a purchase order or a work order.
        """
        rows = self.db.execute(
            select(
                Item.id,
                Item.code,
                Item.name,
                Item.name_ar,
                Item.reorder_level,
                Item.item_type,
                Item.purchase_price,
                func.coalesce(func.sum(StockLayer.quantity_remaining), 0),
            )
            .outerjoin(
                StockLayer,
                (StockLayer.item_id == Item.id)
                & (StockLayer.company_id == self.company_id)
                & (StockLayer.quantity_remaining > 0),
            )
            .where(
                Item.company_id == self.company_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
                Item.reorder_level > 0,
            )
            .group_by(
                Item.id,
                Item.code,
                Item.name,
                Item.name_ar,
                Item.reorder_level,
                Item.item_type,
                Item.purchase_price,
            )
            .order_by(Item.code)
        ).all()

        items = []
        for item_id, code, name, name_ar, reorder_level, item_type, purchase_price, on_hand in rows:
            on_hand = Decimal(str(on_hand or 0))
            reorder_level = Decimal(str(reorder_level or 0))
            if on_hand > reorder_level:
                continue
            shortfall = reorder_level - on_hand
            items.append(
                {
                    "item_id": item_id,
                    "item_code": code,
                    "item_name": name,
                    "item_name_ar": name_ar,
                    "item_type": item_type,
                    "on_hand": on_hand,
                    "reorder_level": reorder_level,
                    "shortfall": shortfall,
                    "estimated_cost": money(shortfall * Decimal(str(purchase_price or 0))),
                }
            )
        items.sort(key=lambda r: r["shortfall"], reverse=True)
        return {
            "items": items,
            "count": len(items),
            "estimated_total_cost": sum((r["estimated_cost"] for r in items), ZERO),
        }

    def item_profitability(self, start: date | None = None, end: date | None = None) -> dict:
        """Revenue, cost and margin per item, from the frozen sale costs.

        Uses the cost stored on each sold line rather than today's cost, so a
        margin reported last month does not move when stock is repriced.
        """
        from ...db.models import Invoice, InvoiceLine
        from ...domain.money import compute_line

        stmt = (
            select(InvoiceLine)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(
                InvoiceLine.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
                InvoiceLine.item_id.isnot(None),
            )
        )
        if start:
            stmt = stmt.where(Invoice.issue_date >= start)
        if end:
            stmt = stmt.where(Invoice.issue_date <= end)

        per_item: dict[int, dict] = {}
        for line in self.db.scalars(stmt):
            amounts = compute_line(line.quantity, line.unit_price, line.discount_rate, line.tax_rate)
            row = per_item.setdefault(
                line.item_id,
                {
                    "item_id": line.item_id,
                    "quantity": Decimal("0"),
                    "revenue": ZERO,
                    "cost": ZERO,
                    "manufactured_cost": ZERO,
                    "purchased_cost": ZERO,
                },
            )
            row["quantity"] += Decimal(str(line.quantity or 0))
            row["revenue"] += amounts.net
            row["cost"] += money(line.cost_amount)
            row["manufactured_cost"] += money(line.cost_manufactured)
            row["purchased_cost"] += money(line.cost_purchased)

        items = []
        for row in per_item.values():
            item = self.items.get(row["item_id"])
            row["item_code"] = item.code
            row["item_name"] = item.name
            row["item_name_ar"] = item.name_ar
            row["profit"] = money(row["revenue"] - row["cost"])
            row["margin_percent"] = (
                (row["profit"] / row["revenue"] * 100).quantize(Decimal("0.01")) if row["revenue"] else ZERO
            )
            row["average_sale_price"] = (
                (row["revenue"] / row["quantity"]).quantize(Decimal("0.01")) if row["quantity"] else ZERO
            )
            items.append(row)
        items.sort(key=lambda r: r["profit"], reverse=True)

        total_revenue = sum((r["revenue"] for r in items), ZERO)
        total_cost = sum((r["cost"] for r in items), ZERO)
        return {
            "items": items,
            "total_revenue": total_revenue,
            "total_cost": total_cost,
            "total_profit": money(total_revenue - total_cost),
            "margin_percent": (
                ((total_revenue - total_cost) / total_revenue * 100).quantize(Decimal("0.01"))
                if total_revenue
                else ZERO
            ),
        }

    def warehouse_stock(self) -> dict:
        """Stock value per warehouse, so a branch or store can be compared."""
        rows = self.db.execute(
            select(
                StockLayer.warehouse_id,
                func.sum(StockLayer.quantity_remaining),
                func.sum(StockLayer.quantity_remaining * StockLayer.unit_cost),
            )
            .where(StockLayer.company_id == self.company_id, StockLayer.quantity_remaining > 0)
            .group_by(StockLayer.warehouse_id)
        ).all()

        names = {w.id: w for w in self.warehouses.list(include_inactive=True)}
        warehouses = []
        for wh_id, quantity, value in rows:
            warehouse = names.get(wh_id)
            warehouses.append(
                {
                    "warehouse_id": wh_id,
                    "code": warehouse.code if warehouse else "",
                    "name": (warehouse.name_ar or warehouse.name) if warehouse else "",
                    "quantity": Decimal(str(quantity or 0)),
                    "value": money(value),
                }
            )
        warehouses.sort(key=lambda r: r["value"], reverse=True)
        return {
            "warehouses": warehouses,
            "total_value": sum((w["value"] for w in warehouses), ZERO),
        }

    def low_stock_alerts(self, limit: int = 50) -> dict:
        """Items whose stock has fallen below their reorder level, worst first."""
        report = self.reorder_report()
        return {
            "items": report["items"][:limit],
            "count": report["count"],
        }
