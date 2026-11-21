"""Inventory: units, items, variants, warehouses and the stock ledger.

Design rules, chosen to mirror the general ledger's invariants:

* Stock on hand is never a stored number. It is always derived from
  ``stock_movements``, which is append-only, so a balance can never disagree
  with the history that produced it.
* Costing is FIFO. Every receipt creates a *layer*; every issue consumes the
  oldest layers first and records exactly which layers it took in
  ``stock_consumptions``, so any cost of sale traces back to the purchase that
  produced it.
* FIFO layers are held company-wide per (item, variant). A warehouse is a
  location dimension over that same pool, so moving goods between warehouses
  relocates quantity without changing valuation.
* Corrections are reversals, never edits.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantMixin, TimestampMixin, utcnow

# An item is bought, made, both, or not stocked at all (a service).
ITEM_TYPES = ("purchased", "manufactured", "both", "service")

# Movement kinds. The sign of ``quantity`` carries the direction; the kind
# carries the business meaning, which is what reports group by.
MOVEMENT_KINDS = (
    "opening",  # opening balance brought forward
    "receipt",  # goods received (purchase, or free-text supply)
    "issue",  # goods issued (sale, or consumption)
    "transfer_in",
    "transfer_out",
    "production_issue",  # raw materials consumed by a work order
    "production_receipt",  # finished goods produced by a work order
    "adjustment_in",  # stock count gain
    "adjustment_out",  # stock count loss
    "damage",  # written off as damaged
)

# Accounts every company's inventory posts to. Kept here so the model layer
# and the services agree on the same codes.
INVENTORY_CODE = "1200"
DAMAGED_CODE = "1210"
WIP_CODE = "1220"

# Kinds that add quantity to the pool and therefore open a FIFO layer.
INBOUND_KINDS = ("opening", "receipt", "transfer_in", "production_receipt", "adjustment_in")

QTY = Numeric(18, 4)  # metres and kilos need decimals
COST = Numeric(18, 6)  # unit cost keeps extra precision for FIFO arithmetic


class UnitOfMeasure(Base, TenantMixin, TimestampMixin):
    """A unit such as متر, كيلو or قطعة. Decimals on quantity make these real."""

    __tablename__ = "units_of_measure"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_units_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    name_ar: Mapped[str] = mapped_column(String(80), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ItemGroup(Base, TenantMixin, TimestampMixin):
    """A category tree for items (e.g. أقمشة > قطن)."""

    __tablename__ = "item_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("item_groups.id"), nullable=True)

    parent = relationship("ItemGroup", remote_side="ItemGroup.id", backref="children")


class Warehouse(Base, TenantMixin, TimestampMixin):
    """A warehouse, which may contain sub-warehouses.

    ``is_group`` marks a node that only exists to hold children (like a main
    warehouse); stock is only ever posted to a leaf. Balances of a parent are
    the sum of its descendants, computed on read.
    """

    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_warehouses_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    name_ar: Mapped[str] = mapped_column(String(120), default="")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # What the warehouse holds: "finished", "raw", "supplies" or "" when unset.
    # Drives the purchases-by-warehouse-type breakdown.
    kind: Mapped[str] = mapped_column(String(20), default="", server_default="", nullable=False)
    address: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    parent = relationship("Warehouse", remote_side="Warehouse.id", backref="children")


class Item(Base, TenantMixin, TimestampMixin):
    """A stock-keeping or service item.

    Items that come in colours or sizes set ``has_variants`` and own a row per
    combination in ``ItemVariant``. Stock is always held against a variant, so
    an item without variants simply has one variant with no attributes.
    """

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_items_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    name_ar: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    item_type: Mapped[str] = mapped_column(String(20), default="purchased", nullable=False)
    item_group_id: Mapped[int | None] = mapped_column(ForeignKey("item_groups.id"), nullable=True)
    base_unit_id: Mapped[int | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)

    # A service is never stocked; everything else is.
    track_inventory: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_variants: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Purchasing and selling defaults. Sale price is a default only: an issued
    # invoice keeps the price it was written with, so later changes never
    # rewrite history.
    purchase_price: Mapped[Decimal] = mapped_column(COST, default=0, nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(COST, default=0, nullable=False)
    tax_code_id: Mapped[int | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    reorder_level: Mapped[Decimal] = mapped_column(QTY, default=0, nullable=False)
    default_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    image_url: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    unit = relationship("UnitOfMeasure")
    group = relationship("ItemGroup")
    variants = relationship(
        "ItemVariant", back_populates="item", cascade="all, delete-orphan", order_by="ItemVariant.id"
    )

    @property
    def barcode(self) -> str:
        """The item's own barcode, held on its first variant.

        Barcodes live per variant because a colour or size scans differently,
        but most items have exactly one variant and the user thinks of the
        code as belonging to the item. Exposing it here keeps the API honest
        without a second column that could disagree with the variant's.
        """
        return self.variants[0].barcode if self.variants else ""

    @barcode.setter
    def barcode(self, value: str) -> None:
        if self.variants:
            self.variants[0].barcode = value or ""


class ItemVariant(Base, TenantMixin, TimestampMixin):
    """One sellable form of an item: a colour, a size, or a combination.

    ``attributes`` stores anything beyond colour/size (e.g. a pattern name) as
    JSON text, so a factory can add dimensions without a migration.
    """

    __tablename__ = "item_variants"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_item_variants_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    colour: Mapped[str] = mapped_column(String(60), default="")
    size: Mapped[str] = mapped_column(String(60), default="")
    attributes: Mapped[str] = mapped_column(Text, default="")  # JSON object
    barcode: Mapped[str] = mapped_column(String(80), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    item = relationship("Item", back_populates="variants")

    @property
    def label(self) -> str:
        parts = [p for p in (self.colour, self.size) if p]
        return " / ".join(parts)


class StockMovement(Base, TenantMixin):
    """An append-only line in the stock ledger.

    ``quantity`` is signed: positive brings goods into the warehouse, negative
    takes them out. ``unit_cost`` is the FIFO cost at the moment of the
    movement, so ``total_cost`` is the value that entered or left the books.
    """

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    movement_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("item_variants.id"), nullable=True, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)

    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(COST, default=0, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    source_type: Mapped[str] = mapped_column(String(30), default="manual")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    reverses_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movements.id"), nullable=True
    )
    reversed_by_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movements.id"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item = relationship("Item")
    variant = relationship("ItemVariant")
    warehouse = relationship("Warehouse")


class StockLayer(Base, TenantMixin):
    """A FIFO cost layer: one per inbound movement.

    ``quantity_remaining`` falls as later issues consume the layer. The sum of
    remaining quantities always equals the total quantity on hand, and the sum
    of ``quantity_remaining * unit_cost`` is the inventory valuation.
    """

    __tablename__ = "stock_layers"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("item_variants.id"), nullable=True, index=True)
    received_at: Mapped[date] = mapped_column(Date, index=True)
    quantity_received: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    quantity_remaining: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(COST, nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    movement_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), default="")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StockConsumption(Base, TenantMixin):
    """Which layer an outbound movement drew from, and how much.

    This is the audit link that makes a FIFO cost defensible: given any cost of
    sale, you can name the purchase batch it came from.
    """

    __tablename__ = "stock_consumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    movement_id: Mapped[int] = mapped_column(
        ForeignKey("stock_movements.id", ondelete="CASCADE"), index=True
    )
    layer_id: Mapped[int] = mapped_column(ForeignKey("stock_layers.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(COST, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    layer = relationship("StockLayer")
