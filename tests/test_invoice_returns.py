"""Returns (credit notes): the mirror of an invoice, in the books and in stock.

A return is what corrects an issued invoice whose goods came back — the client
asked for it because editing a paid invoice is not an option and voiding it
would erase the sale. These tests pin down the four things a return must get
right: it reverses the money, it reverses the stock at the original cost, it
cannot exceed what was sold, and the reports show the net figure.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_api import auth, register


@pytest.fixture
def setup(client):
    """A company with one stocked item and a customer, ready to sell."""
    token = register(client, email="returns@acme.eg", company="شركة المرتجعات")
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
    warehouse = next(w for w in warehouses if w["code"] == "RAW")

    item = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={
            "name": "قميص",
            "item_type": "purchased",
            "base_unit_id": piece["id"],
            "sale_price": "150",
            "purchase_price": "80",
        },
    ).json()
    customer = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "عميل", "kind": "customer"}
    ).json()
    supplier = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "مورد", "kind": "supplier"}
    ).json()
    # 20 units on hand at 80 each, so a sale has a known cost.
    client.post(
        "/api/v1/inventory/movements/receive",
        headers=auth(token),
        json={
            "item_id": item["id"],
            "warehouse_id": warehouse["id"],
            "quantity": "20",
            "unit_cost": "80",
            "movement_date": "2026-11-01",
            "kind": "opening",
        },
    )
    return {
        "token": token,
        "item": item,
        "customer": customer,
        "supplier": supplier,
        "warehouse": warehouse,
    }


def make_invoice(client, ctx, *, kind, contact, lines, issue_date="2026-11-10"):
    response = client.post(
        "/api/v1/invoices",
        headers=auth(ctx["token"]),
        json={"kind": kind, "contact_id": contact["id"], "issue_date": issue_date, "lines": lines},
    )
    assert response.status_code == 201, response.text
    return response.json()


def returnable(client, ctx, invoice_id):
    response = client.get(f"/api/v1/invoices/{invoice_id}/returnable", headers=auth(ctx["token"]))
    assert response.status_code == 200, response.text
    return response.json()


def make_return(client, ctx, invoice_id, lines, issue_date="2026-11-20"):
    return client.post(
        f"/api/v1/invoices/{invoice_id}/returns",
        headers=auth(ctx["token"]),
        json={"issue_date": issue_date, "lines": lines},
    )


def trial_balance(client, ctx):
    response = client.get("/api/v1/reports/trial-balance", headers=auth(ctx["token"]))
    assert response.status_code == 200, response.text
    return response.json()


class TestReturnBasics:
    def test_returnable_reports_what_is_left(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        rows = returnable(client, setup, invoice["id"])
        assert len(rows) == 1
        assert Decimal(rows[0]["remaining"]) == Decimal("10.0000")

    def test_partial_return_creates_a_mirror_document(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        response = make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])
        assert response.status_code == 201, response.text
        ret = response.json()
        assert ret["kind"] == "sales_return"
        assert ret["return_of_id"] == invoice["id"]
        assert Decimal(ret["total"]) == Decimal("600")

    def test_a_return_reduces_what_remains_returnable(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])
        rows = returnable(client, setup, invoice["id"])
        assert Decimal(rows[0]["returned"]) == Decimal("4.0000")
        assert Decimal(rows[0]["remaining"]) == Decimal("6.0000")

    def test_returning_more_than_sold_is_refused(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])
        response = make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "7"}])
        assert response.status_code == 422

    def test_a_return_cannot_itself_be_returned(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        ret = make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}]).json()
        response = make_return(client, setup, ret["id"], [{"line_id": 1, "quantity": "1"}])
        assert response.status_code == 422


class TestReturnStock:
    def test_a_sales_return_puts_goods_back_at_their_original_cost(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "5", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        after_sale = client.get("/api/v1/inventory/summary", headers=auth(setup["token"])).json()
        assert Decimal(after_sale["total_value"]) == Decimal("1200.00")  # 15 left at 80

        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "2"}])

        after_return = client.get("/api/v1/inventory/summary", headers=auth(setup["token"])).json()
        assert Decimal(after_return["total_value"]) == Decimal("1360.00")  # 17 left at 80
        assert trial_balance(client, setup)["balanced"] is True

    def test_a_purchase_return_takes_goods_back_out(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[{"description": "شراء", "quantity": "5", "unit_price": "90", "item_id": setup["item"]["id"]}],
        )
        # 20 opening + 5 bought = 25 on hand, 20 at 80 then 5 at 90
        before = client.get("/api/v1/inventory/summary", headers=auth(setup["token"])).json()
        assert Decimal(before["total_value"]) == Decimal("2050.00")

        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "5"}])

        after = client.get("/api/v1/inventory/summary", headers=auth(setup["token"])).json()
        # Stock leaves oldest-first, the same rule a sale and a void use: the
        # five units come off the 80-cost opening layer, leaving 15 at 80 plus
        # the 5 bought at 90.
        assert Decimal(after["total_value"]) == Decimal("1650.00")
        assert trial_balance(client, setup)["balanced"] is True


class TestReturnReports:
    def test_sales_summary_nets_returns(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])

        summary = client.get("/api/v1/reports/sales-summary", headers=auth(setup["token"])).json()
        assert Decimal(summary["gross"]) == Decimal("900")  # 1500 sold less 600 returned
        assert summary["return_count"] == 1

    def test_ar_aging_is_reduced_by_a_return(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])

        aging = client.get("/api/v1/reports/ar-aging", headers=auth(setup["token"])).json()
        assert Decimal(aging["total"]) == Decimal("900")

    def test_profit_by_source_nets_the_return(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])

        report = client.get("/api/v1/reports/profit-by-source", headers=auth(setup["token"])).json()
        assert Decimal(report["total"]["revenue"]) == Decimal("900")
        assert Decimal(report["total"]["cost"]) == Decimal("480")  # 6 units at 80
        assert Decimal(report["total"]["profit"]) == Decimal("420")


class TestReturnImmutability:
    def test_an_issued_return_cannot_be_edited(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        ret = make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}]).json()
        response = client.patch(
            f"/api/v1/invoices/{ret['id']}",
            headers=auth(setup["token"]),
            json={"notes": "تغيير"},
        )
        assert response.status_code == 409

    def test_an_issued_return_cannot_be_voided(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        ret = make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}]).json()
        response = client.post(
            f"/api/v1/invoices/{ret['id']}/void",
            headers=auth(setup["token"]),
            json={"reason": "خطأ"},
        )
        assert response.status_code == 409

    def test_returns_of_lists_the_documents_against_an_invoice(self, client, setup):
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "بيع", "quantity": "10", "unit_price": "150", "item_id": setup["item"]["id"]}],
        )
        line_id = returnable(client, setup, invoice["id"])[0]["line_id"]
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "4"}])
        make_return(client, setup, invoice["id"], [{"line_id": line_id, "quantity": "2"}])
        response = client.get(f"/api/v1/invoices/{invoice['id']}/returns", headers=auth(setup["token"]))
        assert response.status_code == 200
        assert len(response.json()) == 2
