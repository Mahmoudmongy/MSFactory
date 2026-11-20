"""The modern inventory screen must offer every stock operation the API exposes.

These are static checks against the shipped JavaScript and CSS. The screen is
built at runtime, so a regression here is silent: the tab still opens, it just
stops offering the ability to receive, issue, transfer, damage or count — or it
starts hiding a field the server requires.
"""
from __future__ import annotations

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = STATIC / "app.js"
STYLES_CSS = STATIC / "styles.css"


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def test_inventory_route_covers_all_four_tabs(app_js: str) -> None:
    start = app_js.index("async function renderInventory()")
    body = app_js[start : app_js.index("async function loadInventoryLookups")]
    for key in ("stock", "items", "warehouses", "movements"):
        assert f'key: "{key}"' in body, f"tab {key} is missing"
    for fn in (
        "renderInventoryStock",
        "renderInventoryItems",
        "renderInventoryWarehouses",
        "renderInventoryMovements",
    ):
        assert fn in body, f"{fn} is not wired to a tab"


def test_every_stock_endpoint_has_a_ui_path(app_js: str) -> None:
    """Each write endpoint the API exposes must be reachable from the sheet."""
    for endpoint in ("receive", "issue", "transfer", "damage", "count"):
        assert f'"{endpoint}"' in app_js, f"/inventory/movements/{endpoint} is unreachable"
    assert "`/inventory/movements/${endpoint}`" in app_js


def test_movement_sheet_switches_fields_per_kind(app_js: str) -> None:
    start = app_js.index("function openMovementSheet(")
    body = app_js[start : app_js.index("// ------------------------------------------------- ledger")]

    # A transfer needs a source and a target; everything else needs one store.
    assert 'show("warehouse", kind !== "transfer")' in body
    assert 'show("to_warehouse", kind === "transfer")' in body
    # Only a receipt carries a cost; a count asks for the counted quantity.
    assert 'show("unit_cost", kind === "receive")' in body
    assert 'kind === "count" ? "الكمية المعدودة" : "الكمية"' in body


def test_transfer_posts_both_warehouses_and_rejects_the_same_one(app_js: str) -> None:
    start = app_js.index("function openMovementSheet(")
    body = app_js[start : app_js.index("// ------------------------------------------------- ledger")]
    assert "from_warehouse_id" in body and "to_warehouse_id" in body
    assert "اختر مخزنين مختلفين" in body


def test_movement_sheet_keeps_the_damage_store_out_of_the_way(app_js: str) -> None:
    """DMG sorts first by code; defaulting to it would send goods to write-offs."""
    start = app_js.index("function openMovementSheet(")
    body = app_js[start : app_js.index("// ------------------------------------------------- ledger")]
    assert 'const DAMAGE_CODE = "DMG"' in body
    assert "sort(" in body


def test_item_sheet_can_create_and_edit(app_js: str) -> None:
    start = app_js.index("function openItemSheet(")
    body = app_js[start : app_js.index("async function renderInventoryWarehouses")]
    assert 'method: "PATCH"' in body, "editing an item must PATCH"
    assert 'await api("/inventory/items", { method: "POST", body })' in body
    for field in ("name_ar", "item_type", "base_unit_id", "sale_price", "reorder_level"):
        assert f'name="{field}"' in body, f"field {field} is missing"


def test_warehouse_sheet_handles_the_tree_and_deactivation(app_js: str) -> None:
    start = app_js.index("function openWarehouseSheet(")
    body = app_js[start : app_js.index("async function renderInventoryMovements")]
    assert 'name="parent_id"' in body
    assert 'name="is_group"' in body
    assert 'method: "DELETE"' in body, "deactivating a warehouse uses DELETE"
    assert "إيقاف المخزن" in body and "تفعيل المخزن" in body


def test_stock_movements_are_retried_idempotently(app_js: str) -> None:
    """A double tap on a flaky phone must not post the movement twice."""
    start = app_js.index("function openMovementSheet(")
    body = app_js[start : app_js.index("// ------------------------------------------------- ledger")]
    assert 'Idempotency-Key": idempotencyKey("mov"' in body


def test_warehouse_lookups_are_cached_and_invalidated(app_js: str) -> None:
    assert "inventory: null" in app_js
    assert "store.cache.inventory = {" in app_js
    assert "function refreshInventoryLookups()" in app_js


def test_hidden_attribute_beats_layout_rules(styles_css: str) -> None:
    """The sheet hides fields with `hidden`; a display rule must not override it."""
    assert "[hidden]" in styles_css
    rule = styles_css[styles_css.index("[hidden]") :]
    assert "display: none" in rule.split("}")[0]
