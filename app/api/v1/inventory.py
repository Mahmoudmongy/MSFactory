"""Inventory endpoints: units, items, warehouses and the stock ledger.

Reads are gated on ``*_VIEW`` and writes on ``*_MANAGE``, so a storekeeper can
move stock without being able to rename the chart of accounts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.models import ItemGroup, UnitOfMeasure
from ...db.session import get_session
from ...repositories.inventory import ItemRepository, UnitRepository, WarehouseRepository
from ...services.inventory import InventoryService
from ..v1.deps import Principal, require
from ..v1.schemas import (
    InventorySummary,
    ItemCreate,
    ItemDetailOut,
    ItemGroupCreate,
    ItemGroupOut,
    ItemOut,
    ItemUpdate,
    StockCount,
    StockDamage,
    StockIssue,
    StockMovementOut,
    StockReceive,
    StockRow,
    StockTransfer,
    UnitCreate,
    UnitOut,
    VariantIn,
    VariantOut,
    WarehouseCreate,
    WarehouseOut,
    WarehouseUpdate,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _service(principal: Principal, db: Session) -> InventoryService:
    return InventoryService(db, principal.company_id, principal.user.id)


# ------------------------------------------------------------------- units


@router.get("/units", response_model=list[UnitOut])
def list_units(
    principal: Principal = Depends(require(Permission.ITEM_VIEW)),
    db: Session = Depends(get_session),
):
    return UnitRepository(db, principal.company_id).list(include_inactive=True)


@router.post("/units", response_model=UnitOut, status_code=status.HTTP_201_CREATED)
def create_unit(
    payload: UnitCreate,
    principal: Principal = Depends(require(Permission.ITEM_MANAGE)),
    db: Session = Depends(get_session),
):
    repo = UnitRepository(db, principal.company_id)
    if repo.by_code(payload.code):
        from ...core.errors import ValidationError

        raise ValidationError(f"كود الوحدة {payload.code} مستخدم بالفعل")
    unit = repo.add(UnitOfMeasure(code=payload.code, name=payload.name, name_ar=payload.name_ar))
    db.commit()
    return unit


# -------------------------------------------------------------- item groups


@router.get("/item-groups", response_model=list[ItemGroupOut])
def list_item_groups(
    principal: Principal = Depends(require(Permission.ITEM_VIEW)),
    db: Session = Depends(get_session),
):
    from sqlalchemy import select

    return list(
        db.scalars(
            select(ItemGroup)
            .where(ItemGroup.company_id == principal.company_id)
            .order_by(ItemGroup.name)
        )
    )


@router.post("/item-groups", response_model=ItemGroupOut, status_code=status.HTTP_201_CREATED)
def create_item_group(
    payload: ItemGroupCreate,
    principal: Principal = Depends(require(Permission.ITEM_MANAGE)),
    db: Session = Depends(get_session),
):
    group = ItemGroup(
        company_id=principal.company_id,
        name=payload.name,
        name_ar=payload.name_ar,
        parent_id=payload.parent_id,
    )
    db.add(group)
    db.commit()
    return group


# ------------------------------------------------------------------- items


@router.get("/items", response_model=list[ItemOut])
def list_items(
    search: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    group_id: int | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require(Permission.ITEM_VIEW)),
    db: Session = Depends(get_session),
):
    return ItemRepository(db, principal.company_id).list(
        search=search, item_type=item_type, group_id=group_id, include_inactive=include_inactive
    )


@router.post("/items", response_model=ItemDetailOut, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    principal: Principal = Depends(require(Permission.ITEM_MANAGE)),
    db: Session = Depends(get_session),
):
    service = _service(principal, db)
    item = service.create_item(
        code=payload.code,
        name=payload.name,
        variants=[v.model_dump() for v in payload.variants],
        name_ar=payload.name_ar,
        description=payload.description,
        item_type=payload.item_type,
        item_group_id=payload.item_group_id,
        base_unit_id=payload.base_unit_id,
        track_inventory=payload.track_inventory,
        purchase_price=payload.purchase_price,
        sale_price=payload.sale_price,
        tax_code_id=payload.tax_code_id,
        reorder_level=payload.reorder_level,
        default_warehouse_id=payload.default_warehouse_id,
        image_url=payload.image_url,
        notes=payload.notes,
    )
    db.commit()
    return ItemDetailOut.model_validate(
        {**ItemOut.model_validate(item).model_dump(), "variants": service.items.variants(item.id)}
    )


@router.get("/items/{item_id}", response_model=ItemDetailOut)
def get_item(
    item_id: int,
    principal: Principal = Depends(require(Permission.ITEM_VIEW)),
    db: Session = Depends(get_session),
):
    repo = ItemRepository(db, principal.company_id)
    item = repo.get(item_id)
    return ItemDetailOut.model_validate(
        {**ItemOut.model_validate(item).model_dump(), "variants": repo.variants(item.id)}
    )


@router.patch("/items/{item_id}", response_model=ItemOut)
def update_item(
    item_id: int,
    payload: ItemUpdate,
    principal: Principal = Depends(require(Permission.ITEM_MANAGE)),
    db: Session = Depends(get_session),
):
    repo = ItemRepository(db, principal.company_id)
    item = repo.get(item_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.flush()
    db.commit()
    return item


@router.post("/items/{item_id}/variants", response_model=VariantOut, status_code=status.HTTP_201_CREATED)
def add_variant(
    item_id: int,
    payload: VariantIn,
    principal: Principal = Depends(require(Permission.ITEM_MANAGE)),
    db: Session = Depends(get_session),
):
    variant = _service(principal, db).add_variant(item_id, payload.model_dump())
    db.commit()
    return variant


# -------------------------------------------------------------- warehouses


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require(Permission.ITEM_VIEW)),
    db: Session = Depends(get_session),
):
    return WarehouseRepository(db, principal.company_id).list(include_inactive=include_inactive)


@router.post("/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED)
def create_warehouse(
    payload: WarehouseCreate,
    principal: Principal = Depends(require(Permission.WAREHOUSE_MANAGE)),
    db: Session = Depends(get_session),
):
    warehouse = _service(principal, db).create_warehouse(
        name=payload.name,
        code=payload.code,
        name_ar=payload.name_ar,
        parent_id=payload.parent_id,
        is_group=payload.is_group,
        kind=payload.kind,
        address=payload.address,
        notes=payload.notes,
    )
    db.commit()
    return warehouse


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    principal: Principal = Depends(require(Permission.WAREHOUSE_MANAGE)),
    db: Session = Depends(get_session),
):
    warehouse = _service(principal, db).update_warehouse(
        warehouse_id, **payload.model_dump(exclude_unset=True)
    )
    db.commit()
    return warehouse


# ------------------------------------------------------------------- stock


@router.get("/stock", response_model=list[StockRow])
def stock_on_hand(
    warehouse_id: int | None = Query(default=None),
    item_id: int | None = Query(default=None),
    principal: Principal = Depends(require(Permission.STOCK_VIEW)),
    db: Session = Depends(get_session),
):
    return _service(principal, db).stock_on_hand(warehouse_id=warehouse_id, item_id=item_id)


@router.get("/summary", response_model=InventorySummary)
def inventory_summary(
    principal: Principal = Depends(require(Permission.STOCK_VIEW)),
    db: Session = Depends(get_session),
):
    service = _service(principal, db)
    return InventorySummary(
        total_value=service.inventory_value(),
        item_count=len(service.items.list(include_inactive=True)),
        warehouse_count=len(service.warehouses.list(include_inactive=True)),
        costing_method=service.costing_method(),
    )


@router.get("/movements", response_model=list[StockMovementOut])
def list_movements(
    item_id: int | None = Query(default=None),
    warehouse_id: int | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    principal: Principal = Depends(require(Permission.STOCK_VIEW)),
    db: Session = Depends(get_session),
):
    return _service(principal, db).movements.query(
        item_id=item_id, warehouse_id=warehouse_id, kind=kind, limit=limit
    )


@router.post("/movements/receive", response_model=StockMovementOut, status_code=status.HTTP_201_CREATED)
def receive_stock(
    payload: StockReceive,
    principal: Principal = Depends(require(Permission.STOCK_MANAGE)),
    db: Session = Depends(get_session),
):
    movement = _service(principal, db).receive(
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        quantity=payload.quantity,
        unit_cost=payload.unit_cost,
        movement_date=payload.movement_date,
        variant_id=payload.variant_id,
        kind=payload.kind,
        source_type="manual",
        reference=payload.reference,
        notes=payload.notes,
    )
    db.commit()
    return movement


@router.post("/movements/issue")
def issue_stock(
    payload: StockIssue,
    principal: Principal = Depends(require(Permission.STOCK_MANAGE)),
    db: Session = Depends(get_session),
):
    movement, cost = _service(principal, db).issue(
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        quantity=payload.quantity,
        movement_date=payload.movement_date,
        variant_id=payload.variant_id,
        kind=payload.kind,
        source_type="manual",
        reference=payload.reference,
        notes=payload.notes,
    )
    db.commit()
    return {"movement_id": movement.id, "cost": str(cost)}


@router.post("/movements/transfer", status_code=status.HTTP_201_CREATED)
def transfer_stock(
    payload: StockTransfer,
    principal: Principal = Depends(require(Permission.STOCK_MANAGE)),
    db: Session = Depends(get_session),
):
    out, inbound = _service(principal, db).transfer(
        item_id=payload.item_id,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        quantity=payload.quantity,
        movement_date=payload.movement_date,
        variant_id=payload.variant_id,
        reference=payload.reference,
    )
    db.commit()
    return {"out_movement_id": out.id, "in_movement_id": inbound.id}


@router.post("/movements/damage")
def damage_stock(
    payload: StockDamage,
    principal: Principal = Depends(require(Permission.STOCK_MANAGE)),
    db: Session = Depends(get_session),
):
    movement, cost = _service(principal, db).write_off_damaged(
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        quantity=payload.quantity,
        movement_date=payload.movement_date,
        variant_id=payload.variant_id,
        reason=payload.reason,
    )
    db.commit()
    return {"movement_id": movement.id, "cost": str(cost)}


@router.post("/movements/count")
def count_stock(
    payload: StockCount,
    principal: Principal = Depends(require(Permission.STOCK_MANAGE)),
    db: Session = Depends(get_session),
):
    movement = _service(principal, db).adjust(
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        counted_quantity=payload.counted_quantity,
        movement_date=payload.movement_date,
        variant_id=payload.variant_id,
        notes=payload.notes,
    )
    db.commit()
    return {"movement_id": movement.id if movement else None, "adjusted": movement is not None}
