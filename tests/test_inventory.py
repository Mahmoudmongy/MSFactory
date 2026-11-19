"""Inventory tests: FIFO costing, warehouse tree, transfers and damaged stock.

These exercise real SQL against PostgreSQL, not mocks, because the FIFO engine
depends on row locking and ordered reads that a mock would not reproduce.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.db.models import Company, User
from app.services.inventory import InventoryService
from app.services.onboarding import provision_company

D = Decimal


@pytest.fixture
def company(db):
    owner = User(email="inv-owner@example.com", full_name="Owner", password_hash="x")
    db.add(owner)
    db.flush()
    entity = Company(name="مصنع الاختبار", country="EG", base_currency="EGP")
    provision_company(db, company=entity, owner_id=owner.id)
    db.commit()
    return entity, owner.id


def svc(db, company, user_id=None):
    return InventoryService(db, company.id, user_id)


def test_provisioning_creates_units_and_warehouse_tree(db, company):
    entity, _ = company
    inv = svc(db, entity)
    units = {u.code for u in inv.units.list()}
    assert {"PCS", "MTR", "KGM"} <= units

    warehouses = {w.code: w for w in inv.warehouses.list()}
    assert warehouses["MAIN"].is_group is True
    assert warehouses["RAW"].parent_id == warehouses["MAIN"].id
    assert warehouses["FABRIC"].parent_id == warehouses["MAIN"].id


def test_item_gets_a_default_variant_and_generated_code(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="قماش قطن", base_unit_id=inv.units.by_code("MTR").id)
    db.commit()

    assert item.code.startswith("ITM-")
    assert item.has_variants is False
    assert len(inv.items.variants(item.id)) == 1


def test_variants_by_colour_and_size(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(
        name="تيشيرت",
        variants=[{"colour": "أحمر", "size": "L"}, {"colour": "أزرق", "size": "M"}],
    )
    db.commit()

    variants = inv.items.variants(item.id)
    assert len(variants) == 2
    assert variants[0].colour == "أحمر"
    assert variants[0].label == "أحمر / L"


def test_fifo_consumes_oldest_layer_first(db, company):
    """The core promise: the first batch bought is the first cost consumed."""
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="خيط", track_inventory=True)
    raw = inv.warehouses.by_code("RAW")

    # Two purchases at different prices.
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=100, unit_cost=D("10"),
                movement_date=date(2026, 1, 1), post_journal=False)
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=100, unit_cost=D("14"),
                movement_date=date(2026, 2, 1), post_journal=False)
    db.commit()

    assert inv.layers.quantity_on_hand(item.id) == D("200.0000")
    assert inv.inventory_value() == D("2400.00")

    # Sell 150: 100 @ 10 then 50 @ 14 = 1700.
    _movement, cost = inv.issue(item_id=item.id, warehouse_id=raw.id, quantity=150,
                               movement_date=date(2026, 3, 1), post_journal=False)
    db.commit()

    assert cost == D("1700.00")
    assert inv.layers.quantity_on_hand(item.id) == D("50.0000")
    assert inv.inventory_value() == D("700.00")

    # The remaining 50 must all come from the 14 layer.
    remaining = inv.layers.remaining_layers(item.id)
    assert len(remaining) == 1
    assert remaining[0].unit_cost == D("14.000000")


def test_issue_beyond_stock_is_rejected(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="زر")
    raw = inv.warehouses.by_code("RAW")
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=10, unit_cost=D("1"),
                movement_date=date(2026, 1, 1), post_journal=False)
    db.commit()

    with pytest.raises(ValidationError, match="أكبر من الرصيد"):
        inv.issue(item_id=item.id, warehouse_id=raw.id, quantity=11,
                  movement_date=date(2026, 1, 2), post_journal=False)


def test_cannot_post_stock_to_a_group_warehouse(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="بلاستيك")
    main = inv.warehouses.by_code("MAIN")

    with pytest.raises(ValidationError, match="رئيسي"):
        inv.receive(item_id=item.id, warehouse_id=main.id, quantity=5, unit_cost=D("2"),
                    movement_date=date(2026, 1, 1), post_journal=False)


def test_transfer_moves_quantity_without_changing_value(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="متر قماش")
    raw = inv.warehouses.by_code("RAW")
    fabric = inv.warehouses.by_code("FABRIC")

    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=40, unit_cost=D("25"),
                movement_date=date(2026, 1, 1), post_journal=False)
    db.commit()
    value_before = inv.inventory_value()

    inv.transfer(item_id=item.id, from_warehouse_id=raw.id, to_warehouse_id=fabric.id,
                 quantity=15, movement_date=date(2026, 1, 5))
    db.commit()

    assert inv.inventory_value() == value_before
    assert inv.layers.quantity_on_hand(item.id) == D("40.0000")

    by_warehouse = {r["warehouse_id"]: r for r in inv.stock_on_hand(item_id=item.id)}
    assert by_warehouse[raw.id]["quantity"] == D("25.0000")
    assert by_warehouse[fabric.id]["quantity"] == D("15.0000")


def test_damaged_stock_is_valued_and_kept_as_an_asset(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="قماش تالف")
    raw = inv.warehouses.by_code("RAW")
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=10, unit_cost=D("30"),
                movement_date=date(2026, 1, 1), post_journal=False)
    db.commit()

    _movement, cost = inv.write_off_damaged(item_id=item.id, warehouse_id=raw.id, quantity=4,
                                            movement_date=date(2026, 1, 10), reason="تلف")
    db.commit()

    assert cost == D("120.00")
    # Total value is unchanged: it simply sits in the damaged warehouse now.
    assert inv.inventory_value() == D("300.00")

    damaged = inv.warehouses.by_code("DMG")
    rows = {r["warehouse_id"]: r for r in inv.stock_on_hand(item_id=item.id)}
    assert rows[damaged.id]["quantity"] == D("4.0000")
    assert rows[raw.id]["quantity"] == D("6.0000")


def test_stock_count_shortage_books_shrinkage(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="قطعة غيار")
    raw = inv.warehouses.by_code("RAW")
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=20, unit_cost=D("5"),
                movement_date=date(2026, 1, 1), post_journal=False)
    db.commit()

    inv.adjust(item_id=item.id, warehouse_id=raw.id, counted_quantity=17,
               movement_date=date(2026, 1, 20), notes="جرد")
    db.commit()

    assert inv.layers.quantity_on_hand(item.id) == D("17.0000")
    assert inv.inventory_value() == D("85.00")


def _inventory_account_balance(db, company_id: int) -> Decimal:
    from sqlalchemy import func, select

    from app.db.models import Account, JournalLine

    return db.scalar(
        select(
            func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0)
        )
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.company_id == company_id, Account.code == "1200")
    )


def test_a_standalone_receipt_posts_inventory_against_capital(db, company):
    """An opening balance must reach the ledger, not cancel itself out.

    The counterpart used to default to the inventory account, which posted a
    debit and a credit to 1200 and left the balance sheet showing no stock.
    """
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="رصيد افتتاحي")
    raw = inv.warehouses.by_code("RAW")

    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=10, unit_cost=D("30"),
                movement_date=date(2026, 1, 1), reference="رصيد افتتاحي")
    db.commit()

    assert _inventory_account_balance(db, entity.id) == D("300.0000")
    assert inv.inventory_value() == D("300.00")


def test_an_outbound_movement_without_a_counterpart_books_shrinkage(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="صنف تالف")
    raw = inv.warehouses.by_code("RAW")
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=10, unit_cost=D("10"),
                movement_date=date(2026, 1, 1))
    db.commit()

    inv.issue(item_id=item.id, warehouse_id=raw.id, quantity=4,
              movement_date=date(2026, 1, 5), kind="damage")
    db.commit()

    # 100 in, 40 written off: 60 must remain on the inventory account.
    assert _inventory_account_balance(db, entity.id) == D("60.0000")


def test_on_hand_rows_report_colour_and_value(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="قميص", variants=[{"colour": "أبيض"}, {"colour": "أسود"}])
    raw = inv.warehouses.by_code("RAW")
    white = inv.items.variants(item.id)[0]

    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=3, unit_cost=D("100"),
                movement_date=date(2026, 1, 1), variant_id=white.id, post_journal=False)
    db.commit()

    rows = inv.stock_on_hand(item_id=item.id)
    assert len(rows) == 1
    assert rows[0]["colour"] == "أبيض"
    assert rows[0]["quantity"] == D("3.0000")
    assert rows[0]["value"] == D("300.00")


def test_service_item_cannot_hold_stock(db, company):
    entity, uid = company
    inv = svc(db, entity, uid)
    service = inv.create_item(name="تركيب", item_type="service", track_inventory=False)
    raw = inv.warehouses.by_code("RAW")

    with pytest.raises(ValidationError, match="غير مخزني"):
        inv.receive(item_id=service.id, warehouse_id=raw.id, quantity=1, unit_cost=D("50"),
                    movement_date=date(2026, 1, 1), post_journal=False)


def test_costing_method_defaults_to_fifo_and_is_switchable(db, company):
    """The company chooses FIFO or weighted average from settings."""
    entity, uid = company
    inv = svc(db, entity, uid)
    assert inv.costing_method() == "fifo"

    from app.repositories.core import SettingRepository

    SettingRepository(db, entity.id).set("costing_method", "weighted_average")
    db.flush()
    assert inv.costing_method() == "weighted_average"


def test_weighted_average_prices_at_the_pool_average(db, company):
    """Switching method changes new costs only; it never rewrites old ones."""
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="خام")
    raw = inv.warehouses.by_code("RAW")

    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=100, unit_cost=D("10"),
                movement_date=date(2026, 1, 1), post_journal=False)
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=100, unit_cost=D("14"),
                movement_date=date(2026, 2, 1), post_journal=False)

    from app.repositories.core import SettingRepository

    SettingRepository(db, entity.id).set("costing_method", "weighted_average")
    db.flush()

    # Average of (100@10 + 100@14) is 12, so 50 out costs 600.
    _movement, cost = inv.issue(item_id=item.id, warehouse_id=raw.id, quantity=50,
                                movement_date=date(2026, 3, 1), post_journal=False)
    db.commit()

    assert cost == D("600.00")
    # 150 left, all revalued to the average: 150 * 12 = 1800.
    assert inv.inventory_value() == D("1800.00")

    for layer in inv.layers.remaining_layers(item.id):
        assert layer.unit_cost == D("12.000000")


def test_weighted_average_keeps_valuation_equal_to_quantity_times_average(db, company):
    """Averaging must not leak value: total value always equals qty * average."""
    entity, uid = company
    inv = svc(db, entity, uid)
    item = inv.create_item(name="مادة")
    raw = inv.warehouses.by_code("RAW")

    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=30, unit_cost=D("7"),
                movement_date=date(2026, 1, 1), post_journal=False)
    inv.receive(item_id=item.id, warehouse_id=raw.id, quantity=70, unit_cost=D("11"),
                movement_date=date(2026, 1, 2), post_journal=False)

    from app.repositories.core import SettingRepository

    SettingRepository(db, entity.id).set("costing_method", "weighted_average")
    db.flush()

    expected_value = D("30") * D("7") + D("70") * D("11")  # 980
    for qty in (5, 25, 40):
        inv.issue(item_id=item.id, warehouse_id=raw.id, quantity=qty,
                  movement_date=date(2026, 2, 1), post_journal=False)
    db.commit()

    on_hand = inv.layers.quantity_on_hand(item.id)
    assert on_hand == D("30.0000")
    assert inv.inventory_value() == D("30") * (expected_value / D("100"))

