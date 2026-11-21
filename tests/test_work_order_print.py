"""Printing a production order: the shop-floor copy and its token.

The order is what a workshop carries to the floor, so the page has to name the
product, the quantity and every material — a missing material line is a stopped
job. The token rules are the same as the invoice's: one order, one company.
"""
from __future__ import annotations

import pytest

from tests.test_api import auth, register


@pytest.fixture
def setup(client):
    token = register(client, email="shopfloor@acme.eg", company="مصنع الورشة")
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
    finished = next(w for w in warehouses if w["code"] == "FG")

    product = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={
            "name": "جاكت شتوي",
            "name_ar": "جاكت شتوي",
            "item_type": "manufactured",
            "base_unit_id": piece["id"],
            "sale_price": "900",
        },
    ).json()
    fabric = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={"name": "قماش", "name_ar": "قماش", "item_type": "raw", "base_unit_id": piece["id"]},
    ).json()

    response = client.post(
        "/api/v1/production/work-orders",
        headers=auth(token),
        json={
            "item_id": product["id"],
            "quantity": "25",
            "start_date": "2026-11-10",
            "due_date": "2026-11-20",
            "warehouse_id": finished["id"],
            "notes": "خط الإنتاج الثاني",
            "materials": [{"item_id": fabric["id"], "quantity": "50", "notes": "لون أسود"}],
        },
    )
    assert response.status_code == 201, response.text
    return {"token": token, "order": response.json(), "product": product, "fabric": fabric}


def print_path(client, token, order_id):
    response = client.post(
        f"/api/v1/production/work-orders/{order_id}/print-token", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["path"]


class TestWorkOrderPrint:
    def test_the_page_names_the_product_and_quantity(self, client, setup):
        response = client.get(f"/api/v1{print_path(client, setup['token'], setup['order']['id'])}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        body = response.text
        assert setup["order"]["number"] in body
        assert "جاكت شتوي" in body
        assert "25" in body

    def test_every_material_is_listed(self, client, setup):
        response = client.get(f"/api/v1{print_path(client, setup['token'], setup['order']['id'])}")
        assert "قماش" in response.text
        assert "لون أسود" in response.text

    def test_the_page_is_not_cacheable(self, client, setup):
        """A print URL carries prices and must not sit in a shared cache."""
        response = client.get(f"/api/v1{print_path(client, setup['token'], setup['order']['id'])}")
        assert response.headers["cache-control"] == "no-store"

    def test_a_token_for_one_order_cannot_open_another(self, client, setup):
        other = client.post(
            "/api/v1/production/work-orders",
            headers=auth(setup["token"]),
            json={
                "item_id": setup["product"]["id"],
                "quantity": "5",
                "start_date": "2026-11-12",
                "materials": [],
            },
        ).json()
        stolen = print_path(client, setup["token"], other["id"]).split("t=", 1)[1]
        response = client.get(f"/api/v1/documents/work-order/{setup['order']['id']}?t={stolen}")
        assert response.status_code == 401

    def test_a_bad_token_is_rejected(self, client, setup):
        response = client.get(
            f"/api/v1/documents/work-order/{setup['order']['id']}?t=not-a-token"
        )
        assert response.status_code == 401

    def test_the_token_names_only_a_work_order(self, client, setup):
        response = client.post(
            f"/api/v1/production/work-orders/{setup['order']['id']}/print-token",
            headers=auth(setup["token"]),
        )
        payload = response.json()
        assert payload["path"].startswith("/documents/work-order/")
        assert payload["expires_in"] <= 15 * 60
