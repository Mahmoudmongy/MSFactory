"""Inventory API tests: the endpoints the app screens call."""
from __future__ import annotations

import pytest

from tests.test_api import auth, register


@pytest.fixture
def token(client):
    return register(client, email="stock@acme.eg", company="مصنع أكمي")


@pytest.fixture
def seeded(client, token):
    """One item in the raw-materials warehouse, ready to move."""
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
    raw = next(w for w in warehouses if w["code"] == "RAW")

    item = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={
            "name": "قماش ميلتون",
            "item_type": "raw_material",
            "base_unit_id": piece["id"],
            "purchase_price": "145",
            "sale_price": "200",
        },
    )
    assert item.status_code == 201, item.text
    return {"item": item.json(), "raw": raw, "token": token}


class TestProvisioning:
    def test_default_units_are_available(self, client, token):
        units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
        codes = {u["code"] for u in units}
        assert {"PCS", "MTR", "KGM"} <= codes

    def test_warehouse_tree_is_seeded(self, client, token):
        warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
        by_code = {w["code"]: w for w in warehouses}
        assert by_code["MAIN"]["is_group"] is True
        assert by_code["RAW"]["parent_id"] == by_code["MAIN"]["id"]
        assert by_code["FABRIC"]["parent_id"] == by_code["MAIN"]["id"]
        assert by_code["FG"]["parent_id"] == by_code["MAIN"]["id"]


class TestItems:
    def test_create_item_generates_a_code(self, client, token):
        response = client.post(
            "/api/v1/inventory/items", headers=auth(token), json={"name": "سوستة 60 سم"}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["code"].startswith("ITM-")
        assert len(body["variants"]) == 1

    def test_create_item_with_variants(self, client, token):
        response = client.post(
            "/api/v1/inventory/items",
            headers=auth(token),
            json={
                "name": "تيشيرت",
                "variants": [{"colour": "أحمر", "size": "L"}, {"colour": "أزرق", "size": "M"}],
            },
        )
        assert response.status_code == 201, response.text
        assert len(response.json()["variants"]) == 2

    def test_search_and_update(self, client, token):
        client.post("/api/v1/inventory/items", headers=auth(token), json={"name": "زر أبيض"})
        found = client.get(
            "/api/v1/inventory/items", headers=auth(token), params={"search": "زر"}
        ).json()
        assert len(found) == 1

        updated = client.patch(
            f"/api/v1/inventory/items/{found[0]['id']}",
            headers=auth(token),
            json={"sale_price": "12.5"},
        )
        assert updated.status_code == 200
        assert updated.json()["sale_price"] == "12.5"


class TestStockMovements:
    def test_receive_then_see_on_hand(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        response = client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "100",
                "unit_cost": "145",
                "movement_date": "2026-11-01",
            },
        )
        assert response.status_code == 201, response.text

        rows = client.get(
            "/api/v1/inventory/stock", headers=auth(token), params={"item_id": item["id"]}
        ).json()
        assert len(rows) == 1
        assert rows[0]["quantity"] == "100.0000"
        assert rows[0]["value"] == "14500.00"

    def test_summary_reports_value_and_method(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "10",
                "unit_cost": "100",
                "movement_date": "2026-11-01",
            },
        )
        summary = client.get("/api/v1/inventory/summary", headers=auth(token)).json()
        assert summary["total_value"] == "1000.00"
        assert summary["costing_method"] == "fifo"
        assert summary["warehouse_count"] == 5

    def test_issue_uses_fifo_cost(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        for quantity, cost in (("100", "10"), ("100", "14")):
            client.post(
                "/api/v1/inventory/movements/receive",
                headers=auth(token),
                json={
                    "item_id": item["id"],
                    "warehouse_id": raw["id"],
                    "quantity": quantity,
                    "unit_cost": cost,
                    "movement_date": "2026-11-01",
                },
            )
        issued = client.post(
            "/api/v1/inventory/movements/issue",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "150",
                "movement_date": "2026-11-05",
            },
        )
        assert issued.status_code == 200, issued.text
        assert issued.json()["cost"] == "1700.00"

    def test_issue_beyond_stock_returns_a_clear_error(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        response = client.post(
            "/api/v1/inventory/movements/issue",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "5",
                "movement_date": "2026-11-05",
            },
        )
        assert response.status_code in (409, 422)
        assert "الرصيد" in response.text

    def test_cannot_post_to_the_group_warehouse(self, client, seeded):
        token, item = seeded["token"], seeded["item"]
        main = next(
            w
            for w in client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
            if w["code"] == "MAIN"
        )
        response = client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": main["id"],
                "quantity": "1",
                "unit_cost": "1",
                "movement_date": "2026-11-01",
            },
        )
        assert response.status_code in (400, 422)
        assert "رئيسي" in response.text

    def test_transfer_between_warehouses(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        fabric = next(
            w
            for w in client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
            if w["code"] == "FABRIC"
        )
        client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "40",
                "unit_cost": "25",
                "movement_date": "2026-11-01",
            },
        )
        moved = client.post(
            "/api/v1/inventory/movements/transfer",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "from_warehouse_id": raw["id"],
                "to_warehouse_id": fabric["id"],
                "quantity": "15",
                "movement_date": "2026-11-02",
            },
        )
        assert moved.status_code == 201, moved.text

        rows = client.get(
            "/api/v1/inventory/stock", headers=auth(token), params={"item_id": item["id"]}
        ).json()
        by_warehouse = {r["warehouse_id"]: r for r in rows}
        assert by_warehouse[raw["id"]]["quantity"] == "25.0000"
        assert by_warehouse[fabric["id"]]["quantity"] == "15.0000"

    def test_damage_moves_stock_to_the_damaged_warehouse(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "10",
                "unit_cost": "30",
                "movement_date": "2026-11-01",
            },
        )
        damaged = client.post(
            "/api/v1/inventory/movements/damage",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "4",
                "movement_date": "2026-11-03",
                "reason": "تلف",
            },
        )
        assert damaged.status_code == 200, damaged.text
        assert damaged.json()["cost"] == "120.00"

    def test_movement_history_is_returned(self, client, seeded):
        token, item, raw = seeded["token"], seeded["item"], seeded["raw"]
        client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "5",
                "unit_cost": "2",
                "movement_date": "2026-11-01",
            },
        )
        history = client.get(
            "/api/v1/inventory/movements", headers=auth(token), params={"item_id": item["id"]}
        ).json()
        assert len(history) == 1
        assert history[0]["kind"] == "receipt"


class TestIsolation:
    def test_items_are_not_visible_across_companies(self, client, seeded):
        other = register(client, email="other@acme.eg", company="شركة أخرى")
        assert client.get("/api/v1/inventory/items", headers=auth(other)).json() == []
        assert (
            client.get(
                f"/api/v1/inventory/items/{seeded['item']['id']}", headers=auth(other)
            ).status_code
            == 404
        )
