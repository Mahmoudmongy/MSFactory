"""Inventory service: items, warehouses and the FIFO stock ledger.

The only place stock moves. Every operation follows the same shape:

1. resolve the item and its variant,
2. validate the warehouse can hold stock,
3. append a signed ``StockMovement``,
4. for inbound movements open a FIFO layer; for outbound movements consume
   layers oldest-first and record what was taken,
5. post the matching journal entry so inventory and the books agree.

Because layers are the source of both quantity and value, and movements are
append-only, on-hand quantity and inventory value are always reproducible.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.errors import NotFoundError, ValidationError
from ..db.base import utcnow
from ..db.models import (
    DAMAGED_CODE,
    INBOUND_KINDS,
    INVENTORY_CODE,
    Item,
    ItemVariant,
    StockConsumption,
    StockLayer,
    StockMovement,
    Warehouse,
)
from ..domain.money import EntryLine, to_money
from ..repositories.core import AccountRepository, AuditRepository, SequenceRepository
from ..repositories.inventory import (
    ItemRepository,
    StockLayerRepository,
    StockMovementRepository,
    UnitRepository,
    WarehouseRepository,
)
from .accounting import AccountingService

ZERO = Decimal("0.00")
FOUR = Decimal("0.0001")

# Accounts used to keep inventory and the books in step. INVENTORY_CODE,
# DAMAGED_CODE and WIP_CODE come from the model layer so both agree.
SHRINKAGE_CODE = "5600"
CAPITAL_CODE = "3100"

# Costing methods a company can choose between in settings.
COSTING_METHODS = ("fifo", "weighted_average")

# Layers whose goods came off a work order are "manufactured"; every other
# source is "purchased". Reports split margin on this one distinction.
MANUFACTURED_SOURCE = "production_receipt"

# Warehouse the server creates for write-offs. Never a default home for goods:
# its code sorts first, so "first leaf warehouse" would pick it every time.
DAMAGE_WAREHOUSE_CODE = "DMG"


class InventoryService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.items = ItemRepository(db, company_id)
        self.warehouses = WarehouseRepository(db, company_id)
        self.movements = StockMovementRepository(db, company_id)
        self.layers = StockLayerRepository(db, company_id)
        self.units = UnitRepository(db, company_id)
        self.accounts = AccountRepository(db, company_id)
        self.sequences = SequenceRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)
        self.accounting = AccountingService(db, company_id, user_id)

    # ------------------------------------------------------------------ items

    def create_item(self, *, code: str = "", name: str, variants: list[dict] | None = None, **fields) -> Item:
        if not name.strip():
            raise ValidationError("اسم الصنف مطلوب")
        if not code:
            code = self.sequences.next_number("item", prefix="ITM", padding=5)
        if self.items.by_code(code):
            raise ValidationError(f"كود الصنف {code} مستخدم بالفعل")

        variants = variants or []
        item = Item(
            company_id=self.company_id,
            code=code,
            name=name,
            name_ar=fields.get("name_ar", ""),
            description=fields.get("description", ""),
            item_type=fields.get("item_type", "purchased"),
            item_group_id=fields.get("item_group_id"),
            base_unit_id=fields.get("base_unit_id"),
            track_inventory=fields.get("track_inventory", True),
            has_variants=bool(variants),
            purchase_price=to_money(fields.get("purchase_price", 0)),
            sale_price=to_money(fields.get("sale_price", 0)),
            tax_code_id=fields.get("tax_code_id"),
            reorder_level=fields.get("reorder_level", 0),
            default_warehouse_id=fields.get("default_warehouse_id"),
            image_url=fields.get("image_url", ""),
            notes=fields.get("notes", ""),
        )
        self.db.add(item)
        self.db.flush()

        barcode = fields.get("barcode", "")
        if variants:
            for raw in variants:
                self._add_variant(item, raw)
        else:
            # A variant-free item still needs one row so stock has a target.
            self._add_variant(item, {"barcode": barcode} if barcode else {})

        self.audit.record(
            action="item.create",
            entity_type="item",
            entity_id=item.id,
            user_id=self.user_id,
            summary=f"صنف {item.code} — {item.name}",
        )
        self.db.flush()
        return item

    def _add_variant(self, item: Item, raw: dict) -> ItemVariant:
        colour = (raw.get("colour") or "").strip()
        size = (raw.get("size") or "").strip()
        code = (raw.get("code") or "").strip()
        if not code:
            suffix = "-".join(p for p in (colour, size) if p)
            code = f"{item.code}-{suffix}" if suffix else item.code
        variant = ItemVariant(
            company_id=self.company_id,
            item_id=item.id,
            code=code,
            colour=colour,
            size=size,
            attributes=raw.get("attributes", ""),
            barcode=raw.get("barcode", ""),
        )
        self.db.add(variant)
        self.db.flush()
        return variant

    def add_variant(self, item_id: int, raw: dict) -> ItemVariant:
        """Add a colour/size to an existing item (used from the work-order screen)."""
        item = self.items.get(item_id)
        variant = self._add_variant(item, raw)
        item.has_variants = True
        self.audit.record(
            action="item.variant.add",
            entity_type="item",
            entity_id=item.id,
            user_id=self.user_id,
            summary=f"إضافة مقاس {variant.label or variant.code} للصنف {item.code}",
        )
        return variant

    def resolve_variant(self, item_id: int, variant_id: int | None = None) -> ItemVariant:
        """Return the variant to post against, defaulting to the item's only one."""
        variants = self.db.scalars(
            select(ItemVariant).where(
                ItemVariant.company_id == self.company_id, ItemVariant.item_id == item_id
            )
        ).all()
        if not variants:
            raise NotFoundError("الصنف ليس له أي شكل (variant)")
        if variant_id is not None:
            for variant in variants:
                if variant.id == variant_id:
                    return variant
            raise NotFoundError("الشكل المطلوب لا يتبع هذا الصنف")
        if len(variants) > 1:
            raise ValidationError("الصنف له أكثر من لون/مقاس — يجب تحديد الشكل")
        return variants[0]

    # ------------------------------------------------------------- warehouses

    def create_warehouse(self, *, name: str, code: str = "", parent_id: int | None = None, **fields) -> Warehouse:
        if not name.strip():
            raise ValidationError("اسم المخزن مطلوب")
        if not code:
            code = self.sequences.next_number("warehouse", prefix="WH", padding=3)
        if self.warehouses.by_code(code):
            raise ValidationError(f"كود المخزن {code} مستخدم بالفعل")
        if parent_id:
            parent = self.warehouses.get(parent_id)
            # A warehouse that holds children must stay a group node.
            parent.is_group = True
        warehouse = Warehouse(
            company_id=self.company_id,
            code=code,
            name=name,
            name_ar=fields.get("name_ar", "") or "",
            parent_id=parent_id,
            is_group=fields.get("is_group", False),
            kind=fields.get("kind", "") or "",
            address=fields.get("address", ""),
            notes=fields.get("notes", ""),
        )
        self.db.add(warehouse)
        self.db.flush()
        self.audit.record(
            action="warehouse.create",
            entity_type="warehouse",
            entity_id=warehouse.id,
            user_id=self.user_id,
            summary=f"مخزن {warehouse.code} — {warehouse.name}",
        )
        return warehouse

    def update_warehouse(self, warehouse_id: int, **fields) -> Warehouse:
        warehouse = self.warehouses.get(warehouse_id)
        if "name" in fields and fields["name"] is not None:
            name = fields["name"].strip()
            if not name:
                raise ValidationError("اسم المخزن مطلوب")
            warehouse.name = name
        for key in ("name_ar", "kind", "address", "notes"):
            if fields.get(key) is not None:
                setattr(warehouse, key, fields[key])
        if fields.get("is_active") is not None:
            warehouse.is_active = bool(fields["is_active"])
        if "parent_id" in fields:
            warehouse.parent_id = fields["parent_id"]
        self.db.flush()
        self.audit.record(
            action="warehouse.update",
            entity_type="warehouse",
            entity_id=warehouse.id,
            user_id=self.user_id,
            summary=f"تعديل مخزن {warehouse.code} — {warehouse.name}",
        )
        return warehouse

    def _leaf_warehouse(self, warehouse_id: int) -> Warehouse:
        warehouse = self.warehouses.get(warehouse_id)
        if warehouse.is_group:
            raise ValidationError(
                f"المخزن «{warehouse.name}» مخزن رئيسي يحتوي مخازن فرعية — اختر مخزناً فرعياً"
            )
        return warehouse

    # ------------------------------------------------------------- stock moves

    def receive(
        self,
        *,
        item_id: int,
        warehouse_id: int,
        quantity,
        unit_cost,
        movement_date: date,
        variant_id: int | None = None,
        kind: str = "receipt",
        source_type: str = "manual",
        source_id: int | None = None,
        reference: str = "",
        notes: str = "",
        post_journal: bool = True,
        contra_account_id: int | None = None,
    ) -> StockMovement:
        """Bring goods in and open a FIFO layer at ``unit_cost``."""
        if kind not in INBOUND_KINDS:
            raise ValidationError(f"نوع الحركة {kind} ليس وارداً")
        quantity = Decimal(str(quantity or 0)).quantize(FOUR)
        unit_cost = Decimal(str(unit_cost or 0))
        if quantity <= 0:
            raise ValidationError("الكمية يجب أن تكون أكبر من صفر")
        if unit_cost < 0:
            raise ValidationError("التكلفة لا يمكن أن تكون سالبة")

        item = self.items.get(item_id)
        self._leaf_warehouse(warehouse_id)
        variant = self.resolve_variant(item.id, variant_id)
        if not item.track_inventory:
            raise ValidationError(f"الصنف «{item.name}» غير مخزني (خدمة)")

        total_cost = to_money(quantity * unit_cost)
        movement = StockMovement(
            company_id=self.company_id,
            movement_date=movement_date,
            kind=kind,
            item_id=item.id,
            variant_id=variant.id,
            warehouse_id=warehouse_id,
            quantity=quantity,
            unit_cost=unit_cost.quantize(Decimal("0.000001")),
            total_cost=total_cost,
            source_type=source_type,
            source_id=source_id,
            reference=reference,
            notes=notes,
            created_by_id=self.user_id,
            created_at=utcnow(),
        )
        self.db.add(movement)
        self.db.flush()

        self.db.add(
            StockLayer(
                company_id=self.company_id,
                item_id=item.id,
                variant_id=variant.id,
                received_at=movement_date,
                quantity_received=quantity,
                quantity_remaining=quantity,
                unit_cost=movement.unit_cost,
                warehouse_id=warehouse_id,
                movement_id=movement.id,
                source_type=source_type,
                source_id=source_id,
                created_at=utcnow(),
            )
        )
        self.db.flush()

        if post_journal and total_cost > ZERO:
            self._post_inventory_entry(
                movement=movement,
                amount=total_cost,
                contra_account_id=contra_account_id,
                debit_inventory=True,
            )
        self.db.flush()
        return movement

    def issue(
        self,
        *,
        item_id: int,
        warehouse_id: int,
        quantity,
        movement_date: date,
        variant_id: int | None = None,
        kind: str = "issue",
        source_type: str = "manual",
        source_id: int | None = None,
        reference: str = "",
        notes: str = "",
        post_journal: bool = True,
        contra_account_id: int | None = None,
    ) -> tuple[StockMovement, Decimal]:
        """Take goods out, consuming FIFO layers oldest-first.

        Returns the movement and the cost of what left, which is the figure the
        caller posts to cost of sales or work-in-progress.
        """
        quantity = Decimal(str(quantity or 0)).quantize(FOUR)
        if quantity <= 0:
            raise ValidationError("الكمية يجب أن تكون أكبر من صفر")
        if kind in INBOUND_KINDS:
            raise ValidationError(f"نوع الحركة {kind} ليس صادراً")

        item = self.items.get(item_id)
        self._leaf_warehouse(warehouse_id)
        variant = self.resolve_variant(item.id, variant_id)
        if not item.track_inventory:
            raise ValidationError(f"الصنف «{item.name}» غير مخزني (خدمة)")

        # Two separate checks, because they answer different questions: the
        # company may hold the item while this warehouse is empty, and issuing
        # from the wrong warehouse silently drives its balance negative.
        on_hand = self.layers.quantity_on_hand(item.id, variant.id)
        if quantity > on_hand:
            raise ValidationError(
                f"الكمية المطلوبة {quantity} أكبر من الرصيد المتاح {on_hand} للصنف «{item.name}»"
            )
        in_warehouse = self.layers.quantity_in_warehouse(item.id, warehouse_id, variant.id)
        if quantity > in_warehouse:
            raise ValidationError(
                f"الكمية المطلوبة {quantity} أكبر من رصيد المخزن {in_warehouse} للصنف «{item.name}»"
            )

        consumed, cost_total = self.layers.consume(
            item.id, variant.id, quantity, method=self.costing_method()
        )

        movement = StockMovement(
            company_id=self.company_id,
            movement_date=movement_date,
            kind=kind,
            item_id=item.id,
            variant_id=variant.id,
            warehouse_id=warehouse_id,
            quantity=-quantity,
            unit_cost=(cost_total / quantity).quantize(Decimal("0.000001")) if quantity else ZERO,
            total_cost=cost_total,
            source_type=source_type,
            source_id=source_id,
            reference=reference,
            notes=notes,
            created_by_id=self.user_id,
            created_at=utcnow(),
        )
        self.db.add(movement)
        self.db.flush()

        for layer, taken, unit_cost in consumed:
            self.db.add(
                StockConsumption(
                    company_id=self.company_id,
                    movement_id=movement.id,
                    layer_id=layer.id,
                    quantity=taken,
                    unit_cost=unit_cost,
                    total_cost=to_money(taken * unit_cost),
                )
            )
        self.db.flush()

        if post_journal and cost_total > ZERO:
            self._post_inventory_entry(
                movement=movement,
                amount=cost_total,
                contra_account_id=contra_account_id,
                debit_inventory=False,
            )
        self.db.flush()
        return movement, cost_total

    def transfer(
        self,
        *,
        item_id: int,
        from_warehouse_id: int,
        to_warehouse_id: int,
        quantity,
        movement_date: date,
        variant_id: int | None = None,
        reference: str = "",
    ) -> tuple[StockMovement, StockMovement]:
        """Move goods between warehouses.

        Valuation does not change: FIFO layers stay company-wide per item, and
        the pair of movements nets to zero quantity and zero value. This is why
        a transfer needs no journal entry.
        """
        if from_warehouse_id == to_warehouse_id:
            raise ValidationError("لا يمكن التحويل لنفس المخزن")
        out, cost = self.issue(
            item_id=item_id,
            warehouse_id=from_warehouse_id,
            quantity=quantity,
            movement_date=movement_date,
            variant_id=variant_id,
            kind="transfer_out",
            source_type="transfer",
            reference=reference,
            post_journal=False,
        )
        quantity_dec = -out.quantity
        unit_cost = (cost / quantity_dec) if quantity_dec else ZERO
        inb = self.receive(
            item_id=item_id,
            warehouse_id=to_warehouse_id,
            quantity=quantity_dec,
            unit_cost=unit_cost,
            movement_date=movement_date,
            variant_id=variant_id,
            kind="transfer_in",
            source_type="transfer",
            reference=reference,
            post_journal=False,
        )
        return out, inb

    def write_off_damaged(
        self,
        *,
        item_id: int,
        warehouse_id: int,
        quantity,
        movement_date: date,
        variant_id: int | None = None,
        reason: str = "",
    ) -> tuple[StockMovement, Decimal]:
        """Move goods to the damaged warehouse and book the loss.

        The quantity stays an asset but lands under «هالك المخزن», so the loss
        is visible without being confused with goods that are still sellable.
        """
        damaged = self.warehouses.by_code("DMG")
        if not damaged:
            damaged = self.create_warehouse(name="هالك المخزن", code="DMG", parent_id=None)

        out, cost = self.issue(
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity=quantity,
            movement_date=movement_date,
            variant_id=variant_id,
            kind="damage",
            source_type="damage",
            notes=reason,
            post_journal=False,
        )
        quantity_dec = -out.quantity
        unit_cost = (cost / quantity_dec) if quantity_dec else ZERO
        self.receive(
            item_id=item_id,
            warehouse_id=damaged.id,
            quantity=quantity_dec,
            unit_cost=unit_cost,
            movement_date=movement_date,
            variant_id=variant_id,
            kind="adjustment_in",
            source_type="damage",
            notes=reason,
            post_journal=False,
        )

        damaged_account = self.accounts.by_code(DAMAGED_CODE)
        inventory_account = self.accounts.by_code(INVENTORY_CODE)
        if damaged_account and inventory_account and cost > ZERO:
            self.accounting.post(
                entry_date=movement_date,
                lines=[
                    EntryLine(
                        account_id=damaged_account.id,
                        debit=cost,
                        description=f"هالك مخزن: {reason}".strip(": "),
                    ),
                    EntryLine(
                        account_id=inventory_account.id,
                        credit=cost,
                        description="تحويل مخزون إلى هالك",
                    ),
                ],
                memo=f"Damaged goods — {reason}" if reason else "Damaged goods",
                source_type="stock_damage",
                source_id=out.id,
            )
        return out, cost

    def adjust(
        self,
        *,
        item_id: int,
        warehouse_id: int,
        counted_quantity,
        movement_date: date,
        variant_id: int | None = None,
        notes: str = "",
    ) -> StockMovement | None:
        """Reconcile the books to a physical count.

        A shortfall is valued at FIFO cost and booked as shrinkage; a surplus
        enters at the item's latest known cost so valuation stays sensible.
        """
        item = self.items.get(item_id)
        variant = self.resolve_variant(item.id, variant_id)
        counted = Decimal(str(counted_quantity or 0)).quantize(FOUR)
        # A count is physical: it measures one warehouse, so the delta has to be
        # against that warehouse's balance, not the company total.
        current = self.layers.quantity_in_warehouse(item.id, warehouse_id, variant.id)
        delta = counted - current
        if delta == 0:
            return None

        if delta > 0:
            unit_cost = self.layers.latest_unit_cost(item.id, variant.id) or to_money(item.purchase_price)
            return self.receive(
                item_id=item.id,
                warehouse_id=warehouse_id,
                quantity=delta,
                unit_cost=unit_cost,
                movement_date=movement_date,
                variant_id=variant.id,
                kind="adjustment_in",
                source_type="stock_count",
                notes=notes,
                contra_account_id=self._shrinkage_account_id(),
            )

        movement, cost = self.issue(
            item_id=item.id,
            warehouse_id=warehouse_id,
            quantity=-delta,
            movement_date=movement_date,
            variant_id=variant.id,
            kind="adjustment_out",
            source_type="stock_count",
            notes=notes,
            post_journal=False,
        )
        shrinkage = self._shrinkage_account_id()
        inventory = self.accounts.by_code(INVENTORY_CODE)
        if shrinkage and inventory and cost > ZERO:
            self.accounting.post(
                entry_date=movement_date,
                lines=[
                    EntryLine(account_id=shrinkage, debit=cost, description="عجز جرد مخزون"),
                    EntryLine(account_id=inventory.id, credit=cost, description="عجز جرد مخزون"),
                ],
                memo="Stock count shortage",
                source_type="stock_count",
                source_id=movement.id,
            )
        return movement

    def _shrinkage_account_id(self) -> int | None:
        account = self.accounts.by_code(SHRINKAGE_CODE)
        return account.id if account else None

    def _post_inventory_entry(
        self,
        *,
        movement: StockMovement,
        amount: Decimal,
        contra_account_id: int | None,
        debit_inventory: bool,
    ) -> None:
        """Keep inventory and the ledger in step for a standalone stock move.

        Document flows (invoices, work orders) post their own, richer entries
        and pass ``post_journal=False`` here to avoid double counting.
        """
        inventory = self.accounts.by_code(INVENTORY_CODE)
        if not inventory:
            return
        contra = None
        if contra_account_id:
            contra = self.accounts.get(contra_account_id)
        if contra is None:
            # A standalone inbound move with no stated counterpart is an
            # opening balance, so it is funded by capital. An outbound one is a
            # write-off. Falling back to the inventory account itself would post
            # a debit and a credit to the same account, leaving inventory out of
            # the books entirely.
            fallback = CAPITAL_CODE if debit_inventory else SHRINKAGE_CODE
            contra = self.accounts.by_code(fallback)
        if contra is None or contra.id == inventory.id:
            return

        if debit_inventory:
            lines = [
                EntryLine(account_id=inventory.id, debit=amount, description=movement.notes or "مخزون"),
                EntryLine(account_id=contra.id, credit=amount, description=movement.notes or "مخزون"),
            ]
        else:
            lines = [
                EntryLine(account_id=contra.id, debit=amount, description=movement.notes or "مخزون"),
                EntryLine(account_id=inventory.id, credit=amount, description=movement.notes or "مخزون"),
            ]
        self.accounting.post(
            entry_date=movement.movement_date,
            lines=lines,
            memo=f"Stock {movement.kind} — {movement.reference}",
            reference=movement.reference,
            source_type="stock_movement",
            source_id=movement.id,
        )

    # -------------------------------------------------------------- reporting

    def costing_method(self) -> str:
        """The company's inventory costing method, from settings.

        Read on every issue so a change in settings takes effect immediately
        for new movements; already-posted costs are never rewritten.
        """
        from ..repositories.core import SettingRepository

        method = SettingRepository(self.db, self.company_id).get("costing_method", "fifo")
        return method if method in COSTING_METHODS else "fifo"

    def origin_split(self, movement_id: int) -> dict[str, dict[str, Decimal]]:
        """Split a movement by origin: how much, and at what cost, was made vs bought.

        Goods made by a work order enter stock through a ``production_receipt``
        layer; anything else was bought. Reading the split from the layers the
        movement actually consumed keeps it right even when one issue draws on
        both kinds at once, which is exactly what a mixed sale does.

        Both quantity and cost are returned because they answer different
        questions: quantity divides revenue (every unit sells at one price),
        cost measures the margin.
        """
        rows = self.db.execute(
            select(
                StockLayer.source_type,
                func.sum(StockConsumption.quantity),
                func.sum(StockConsumption.total_cost),
            )
            .join(StockLayer, StockLayer.id == StockConsumption.layer_id)
            .where(
                StockConsumption.company_id == self.company_id,
                StockConsumption.movement_id == movement_id,
            )
            .group_by(StockLayer.source_type)
        ).all()
        split = {
            "manufactured": {"quantity": Decimal("0.0000"), "cost": ZERO},
            "purchased": {"quantity": Decimal("0.0000"), "cost": ZERO},
        }
        for source_type, quantity, total in rows:
            key = "manufactured" if source_type == MANUFACTURED_SOURCE else "purchased"
            split[key]["quantity"] += Decimal(str(quantity or 0))
            split[key]["cost"] += to_money(total)
        return split

    def resolve_warehouse(
        self, item: Item, warehouse_id: int | None, *, require_stock: bool = False
    ) -> int:
        """Pick the warehouse to move stock in or out of.

        An explicit choice wins; otherwise the item's default; otherwise a leaf
        warehouse that actually holds the item, and only then the first leaf.
        ``require_stock`` makes the last fallback an error instead of guessing,
        which is what a sale needs: posting a sale out of an empty warehouse
        would drive that balance negative.
        """
        if warehouse_id:
            self._leaf_warehouse(warehouse_id)
            return warehouse_id
        if item.default_warehouse_id:
            return item.default_warehouse_id

        # «هالك المخزن» is created for write-offs and must never be picked as a
        # default home for goods — its code sorts first, so a naive "first leaf"
        # would send every unstamped sale there.
        leaves = [w for w in self.warehouses.list() if not w.is_group]
        leaves = [w for w in leaves if w.code != DAMAGE_WAREHOUSE_CODE]
        if not leaves:
            raise ValidationError("لا يوجد مخزن فرعي يمكن الصرف منه — أنشئ مخزناً أولاً")

        holding = [w for w in leaves if self.layers.quantity_in_warehouse(item.id, w.id) > ZERO]
        if holding:
            return holding[0].id
        if require_stock:
            raise ValidationError(
                f"الصنف «{item.name}» غير موجود في أي مخزن — استلمه أولاً أو حدد المخزن"
            )
        return leaves[0].id

    def stock_on_hand(self, *, warehouse_id: int | None = None, item_id: int | None = None) -> list[dict]:
        return self.layers.on_hand_rows(warehouse_id=warehouse_id, item_id=item_id)

    def inventory_value(self, *, warehouse_id: int | None = None) -> Decimal:
        total = self.layers.value_on_hand(warehouse_id=warehouse_id)
        return to_money(total)

    def movements_for_item(self, item_id: int, variant_id: int | None = None) -> list[StockMovement]:
        return self.movements.for_item(item_id, variant_id)

