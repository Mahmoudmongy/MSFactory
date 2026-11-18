"""Editing invoices and running production through work orders.

Two questions the client asked directly drive these tests:

* "Can I fix an invoice after saving it?" — yes. A draft is free to change; an
  issued invoice is edited by reversing its entry and stock, then reposting, so
  the books never hold two contradictory versions.
* "Can I run production?" — yes. A work order consumes materials, accumulates
  their true FIFO cost, and receives the finished good so a later sale reports
  that cost as *manufactured*.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_api import auth, register


def _codes(token, client) -> dict:
    accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
    return {a["code"]: a["id"] for a in accounts}


@pytest.fixture
def setup(client):
    token = register(client, email="prod@acme.eg", company="مصنع الإنتاج")
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
    raw = next(w for w in warehouses if w["code"] == "RAW")
    finished = next(w for w in warehouses if w["code"] == "FG")

    fabric = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={"name": "قماش", "item_type": "purchased", "base_unit_id": piece["id"]},
    ).json()
    jacket = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={
            "name": "جاكت مصنّع",
            "item_type": "manufactured",
            "base_unit_id": piece["id"],
            "sale_price": "1000",
        },
    ).json()
    customer = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "عميل الجملة", "kind": "customer"}
    ).json()
    supplier = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "مورد الأقمشة", "kind": "supplier"}
    ).json()
    return {
        "token": token,
        "fabric": fabric,
        "jacket": jacket,
        "customer": customer,
        "supplier": supplier,
        "raw": raw,
        "finished": finished,
        "accounts": _codes(token, client),
    }


def buy_fabric(client, ctx, *, quantity="100", price="50", date="2026-11-01"):
    response = client.post(
        "/api/v1/invoices",
        headers=auth(ctx["token"]),
        json={
            "kind": "purchase",
            "contact_id": ctx["supplier"]["id"],
            "issue_date": date,
            "lines": [
                {
                    "description": "قماش",
                    "quantity": quantity,
                    "unit_price": price,
                    "item_id": ctx["fabric"]["id"],
                    "warehouse_id": ctx["raw"]["id"],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_sale(client, ctx, *, quantity="2", price="1000", date="2026-11-10"):
    response = client.post(
        "/api/v1/invoices",
        headers=auth(ctx["token"]),
        json={
            "kind": "sales",
            "contact_id": ctx["customer"]["id"],
            "issue_date": date,
            "lines": [
                {
                    "description": "جاكت",
                    "quantity": quantity,
                    "unit_price": price,
                    "item_id": ctx["jacket"]["id"],
                    "warehouse_id": ctx["finished"]["id"],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def stock_jacket(client, ctx, *, quantity="10", cost="600", date="2026-11-01"):
    """Bring finished jackets into stock so a sale has something to issue."""
    response = client.post(
        "/api/v1/invoices",
        headers=auth(ctx["token"]),
        json={
            "kind": "purchase",
            "contact_id": ctx["supplier"]["id"],
            "issue_date": date,
            "lines": [
                {
                    "description": "جاكت جاهز",
                    "quantity": quantity,
                    "unit_price": cost,
                    "item_id": ctx["jacket"]["id"],
                    "warehouse_id": ctx["finished"]["id"],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def stock_qty(client, ctx, item_id):
    rows = client.get(
        "/api/v1/inventory/stock", headers=auth(ctx["token"]), params={"item_id": item_id}
    ).json()
    return sum(Decimal(r["quantity"]) for r in rows)


class TestEditInvoice:
    def test_a_draft_can_be_rewritten_without_touching_the_books(self, client, setup):
        created = client.post(
            "/api/v1/invoices",
            headers=auth(setup["token"]),
            json={
                "kind": "sales",
                "contact_id": setup["customer"]["id"],
                "issue_date": "2026-11-10",
                "status": "draft",
                "lines": [{"description": "بند", "quantity": "1", "unit_price": "100"}],
            },
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["status"] == "draft"

        # A draft posts nothing, so no journal entry exists yet.
        journal = client.get("/api/v1/journal", headers=auth(setup["token"])).json()
        assert all(e["source_id"] != draft["id"] for e in journal)

        edited = client.patch(
            f"/api/v1/invoices/{draft['id']}",
            headers=auth(setup["token"]),
            json={
                "contact_id": setup["customer"]["id"],
                "issue_date": "2026-11-11",
                "lines": [{"description": "بند معدّل", "quantity": "3", "unit_price": "200"}],
            },
        )
        assert edited.status_code == 200, edited.text
        body = edited.json()
        assert body["status"] == "draft"
        assert Decimal(body["subtotal"]) == Decimal("600")
        assert body["lines"][0]["description"] == "بند معدّل"

    def test_issuing_a_draft_posts_it(self, client, setup):
        draft = client.post(
            "/api/v1/invoices",
            headers=auth(setup["token"]),
            json={
                "kind": "sales",
                "contact_id": setup["customer"]["id"],
                "issue_date": "2026-11-10",
                "status": "draft",
                "lines": [{"description": "بند", "quantity": "1", "unit_price": "100"}],
            },
        ).json()
        issued = client.post(f"/api/v1/invoices/{draft['id']}/issue", headers=auth(setup["token"]))
        assert issued.status_code == 200, issued.text
        assert issued.json()["status"] == "issued"

    def test_editing_an_issued_invoice_reverses_and_reposts(self, client, setup):
        stock_jacket(client, setup, quantity="10", cost="600")
        invoice = make_sale(client, setup, quantity="2", price="1000")

        # Two jackets left the warehouse for the sale.
        assert stock_qty(client, setup, setup["jacket"]["id"]) == Decimal("8.0000")

        edited = client.patch(
            f"/api/v1/invoices/{invoice['id']}",
            headers=auth(setup["token"]),
            json={"lines": [{"description": "جاكت", "quantity": "5", "unit_price": "900"}]},
        )
        assert edited.status_code == 200, edited.text
        body = edited.json()
        assert body["status"] == "issued"
        assert Decimal(body["total"]) == Decimal("4500")

        # The old entry is reversed, not deleted: both versions stay in the log.
        journal = client.get("/api/v1/journal", headers=auth(setup["token"])).json()
        reversals = [e for e in journal if e["source_type"] == "reversal"]
        assert reversals, "editing an issued invoice must post a reversal"

    def test_an_invoice_with_payments_cannot_be_edited(self, client, setup):
        stock_jacket(client, setup, quantity="10", cost="600")
        invoice = make_sale(client, setup)
        cash = client.get("/api/v1/accounts", headers=auth(setup["token"])).json()
        cash_id = next(a["id"] for a in cash if a["code"] == "1010")
        paid = client.post(
            "/api/v1/payments",
            headers=auth(setup["token"]),
            json={
                "kind": "receive",
                "contact_id": setup["customer"]["id"],
                "date": "2026-11-12",
                "amount": "500",
                "bank_account_id": cash_id,
                "invoice_id": invoice["id"],
            },
        )
        assert paid.status_code in (200, 201), paid.text

        blocked = client.patch(
            f"/api/v1/invoices/{invoice['id']}",
            headers=auth(setup["token"]),
            json={"lines": [{"description": "محاولة", "quantity": "1", "unit_price": "1"}]},
        )
        assert blocked.status_code == 409


class TestWorkOrders:
    def test_production_consumes_materials_and_stocks_the_finished_good(self, client, setup):
        buy_fabric(client, setup, quantity="100", price="50")
        assert stock_qty(client, setup, setup["fabric"]["id"]) == Decimal("100.0000")

        order = client.post(
            "/api/v1/production/work-orders",
            headers=auth(setup["token"]),
            json={
                "item_id": setup["jacket"]["id"],
                "quantity": "10",
                "start_date": "2026-11-05",
                "warehouse_id": setup["finished"]["id"],
                "materials": [{"item_id": setup["fabric"]["id"], "quantity": "30"}],
            },
        )
        assert order.status_code == 201, order.text
        order_id = order.json()["id"]

        started = client.post(
            f"/api/v1/production/work-orders/{order_id}/start", headers=auth(setup["token"])
        )
        assert started.status_code == 200, started.text
        # Materials left stock: 30 metres at 50 each.
        assert stock_qty(client, setup, setup["fabric"]["id"]) == Decimal("70.0000")

        completed = client.post(
            f"/api/v1/production/work-orders/{order_id}/complete",
            headers=auth(setup["token"]),
            json={"produced_quantity": "10"},
        )
        assert completed.status_code == 200, completed.text
        body = completed.json()
        assert body["status"] == "completed"
        assert Decimal(body["material_cost"]) == Decimal("1500")
        assert Decimal(body["unit_cost"]) == Decimal("150")
        assert stock_qty(client, setup, setup["jacket"]["id"]) == Decimal("10.0000")

    def test_a_sale_of_made_goods_reports_its_cost_as_manufactured(self, client, setup):
        buy_fabric(client, setup, quantity="100", price="50")
        order = client.post(
            "/api/v1/production/work-orders",
            headers=auth(setup["token"]),
            json={
                "item_id": setup["jacket"]["id"],
                "quantity": "10",
                "start_date": "2026-11-05",
                "warehouse_id": setup["finished"]["id"],
                "materials": [{"item_id": setup["fabric"]["id"], "quantity": "30"}],
            },
        ).json()
        client.post(
            f"/api/v1/production/work-orders/{order['id']}/start", headers=auth(setup["token"])
        )
        client.post(
            f"/api/v1/production/work-orders/{order['id']}/complete",
            headers=auth(setup["token"]),
            json={"produced_quantity": "10"},
        )

        make_sale(client, setup, quantity="2", price="1000")
        report = client.get(
            "/api/v1/reports/profit-by-source",
            headers=auth(setup["token"]),
            params={"start": "2026-11-01", "end": "2026-11-30"},
        )
        assert report.status_code == 200, report.text
        made = report.json()["manufactured"]
        assert Decimal(str(made["cost"])) == Decimal("300")
        assert Decimal(str(made["revenue"])) == Decimal("2000")

    def test_cancelling_a_started_order_returns_the_materials(self, client, setup):
        buy_fabric(client, setup, quantity="100", price="50")
        order = client.post(
            "/api/v1/production/work-orders",
            headers=auth(setup["token"]),
            json={
                "item_id": setup["jacket"]["id"],
                "quantity": "10",
                "start_date": "2026-11-05",
                "warehouse_id": setup["finished"]["id"],
                "materials": [{"item_id": setup["fabric"]["id"], "quantity": "30"}],
            },
        ).json()
        client.post(f"/api/v1/production/work-orders/{order['id']}/start", headers=auth(setup["token"]))
        assert stock_qty(client, setup, setup["fabric"]["id"]) == Decimal("70.0000")

        cancelled = client.post(
            f"/api/v1/production/work-orders/{order['id']}/cancel",
            headers=auth(setup["token"]),
            json={"reason": "توقف الخط"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert stock_qty(client, setup, setup["fabric"]["id"]) == Decimal("100.0000")

    def test_a_completed_order_cannot_be_cancelled(self, client, setup):
        buy_fabric(client, setup)
        order = client.post(
            "/api/v1/production/work-orders",
            headers=auth(setup["token"]),
            json={
                "item_id": setup["jacket"]["id"],
                "quantity": "5",
                "start_date": "2026-11-05",
                "warehouse_id": setup["finished"]["id"],
                "materials": [{"item_id": setup["fabric"]["id"], "quantity": "10"}],
            },
        ).json()
        client.post(f"/api/v1/production/work-orders/{order['id']}/start", headers=auth(setup["token"]))
        client.post(
            f"/api/v1/production/work-orders/{order['id']}/complete",
            headers=auth(setup["token"]),
            json={"produced_quantity": "5"},
        )
        blocked = client.post(
            f"/api/v1/production/work-orders/{order['id']}/cancel",
            headers=auth(setup["token"]),
            json={"reason": "خطأ"},
        )
        assert blocked.status_code == 409

    def test_production_keeps_the_books_balanced(self, client, setup):
        buy_fabric(client, setup, quantity="100", price="50")
        order = client.post(
            "/api/v1/production/work-orders",
            headers=auth(setup["token"]),
            json={
                "item_id": setup["jacket"]["id"],
                "quantity": "10",
                "start_date": "2026-11-05",
                "warehouse_id": setup["finished"]["id"],
                "materials": [{"item_id": setup["fabric"]["id"], "quantity": "30"}],
            },
        ).json()
        client.post(f"/api/v1/production/work-orders/{order['id']}/start", headers=auth(setup["token"]))
        client.post(
            f"/api/v1/production/work-orders/{order['id']}/complete",
            headers=auth(setup["token"]),
            json={"produced_quantity": "10"},
        )
        trial = client.get("/api/v1/reports/trial-balance", headers=auth(setup["token"])).json()
        assert Decimal(str(trial["total_debit"])) == Decimal(str(trial["total_credit"]))
