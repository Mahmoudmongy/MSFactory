"""Invoices and inventory together: cost of sale, and profit by origin.

The point of these tests is the business question the client actually asked:
"how do I know my profit on what I made versus what I bought?" The answer is
that every sale draws its cost from the FIFO layers it consumed, and each layer
remembers whether the goods came off a work order or a supplier invoice. So a
single invoice that sells both kinds reports each margin separately, with no
one having to label the invoice.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.test_api import auth, register


@pytest.fixture
def setup(client):
    """A company with one item and its customer, ready to sell."""
    token = register(client, email="profit@acme.eg", company="مصنع الأرباح")
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
    raw = next(w for w in warehouses if w["code"] == "RAW")
    finished = next(w for w in warehouses if w["code"] == "FG")

    item = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={
            "name": "جاكت شتوي",
            "item_type": "both",
            "base_unit_id": piece["id"],
            "sale_price": "1000",
        },
    ).json()
    customer = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "أحمد السباخي", "kind": "customer"}
    ).json()
    supplier = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "مصنع الأقمشة", "kind": "supplier"}
    ).json()
    return {
        "token": token,
        "item": item,
        "customer": customer,
        "supplier": supplier,
        "raw": raw,
        "finished": finished,
    }


def make_invoice(client, ctx, *, kind, contact, lines, issue_date="2026-11-10"):
    response = client.post(
        "/api/v1/invoices",
        headers=auth(ctx["token"]),
        json={
            "kind": kind,
            "contact_id": contact["id"],
            "issue_date": issue_date,
            "lines": lines,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def seed_layer(item_id, warehouse_id, *, quantity, unit_cost, source_type, received_at="2026-11-01"):
    """Drop a FIFO layer straight into the pool.

    A completed work order would normally create the ``production_receipt``
    layer; this stands in for one so the profit split can be tested before the
    production module exists.
    """
    from sqlalchemy import select

    from app.db.models import Item, ItemVariant, StockLayer, StockMovement
    from app.db.session import SessionLocal
    from app.repositories.inventory import StockLayerRepository

    session = SessionLocal()
    try:
        company_id = session.scalar(select(Item.company_id).where(Item.id == item_id))
        variant_id = session.scalar(
            select(ItemVariant.id).where(ItemVariant.item_id == item_id).limit(1)
        )
        repo = StockLayerRepository(session, company_id)
        layer = StockLayer(
            company_id=company_id,
            item_id=item_id,
            variant_id=variant_id,
            received_at=received_at,
            quantity_received=Decimal(str(quantity)),
            quantity_remaining=Decimal(str(quantity)),
            unit_cost=Decimal(str(unit_cost)),
            warehouse_id=warehouse_id,
            source_type=source_type,
        )
        repo.db.add(layer)
        # The layer alone is invisible to the warehouse balance, which is read
        # from the movement ledger; the two must be seeded together or the sale
        # that follows looks like an issue from an empty warehouse.
        repo.db.add(
            StockMovement(
                company_id=company_id,
                movement_date=date.fromisoformat(received_at),
                kind="receipt",
                item_id=item_id,
                variant_id=variant_id,
                warehouse_id=warehouse_id,
                quantity=Decimal(str(quantity)),
                unit_cost=Decimal(str(unit_cost)),
                total_cost=Decimal(str(quantity)) * Decimal(str(unit_cost)),
                source_type=source_type,
                reference="seed",
            )
        )
        session.commit()
    finally:
        session.close()


class TestPurchaseFeedsStock:
    def test_a_purchase_invoice_receives_stock_at_the_price_paid(self, client, setup):
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "جاكت مشترى",
                    "quantity": "10",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["raw"]["id"],
                }
            ],
        )
        rows = client.get(
            "/api/v1/inventory/stock",
            headers=auth(setup["token"]),
            params={"item_id": setup["item"]["id"]},
        ).json()
        assert sum(Decimal(r["quantity"]) for r in rows) == Decimal("10.0000")
        assert sum(Decimal(r["value"]) for r in rows) == Decimal("6000.00")

    def test_a_purchase_posts_to_inventory_not_to_expense(self, client, setup):
        """Unsold goods are an asset; only a sale turns them into a cost."""
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "جاكت مشترى",
                    "quantity": "10",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["raw"]["id"],
                }
            ],
        )
        accounts = client.get("/api/v1/accounts", headers=auth(setup["token"])).json()
        inventory = next(a for a in accounts if a["code"] == "1200")
        balance = client.get(
            f"/api/v1/accounts/{inventory['id']}/balance", headers=auth(setup["token"])
        ).json()["balance"]
        assert Decimal(str(balance)) == Decimal("6000.00")


class TestSaleBooksRealCost:
    def test_a_sale_issues_stock_and_books_cost_of_sale(self, client, setup):
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "شراء",
                    "quantity": "10",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع",
                    "quantity": "4",
                    "unit_price": "1000",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        rows = client.get(
            "/api/v1/inventory/stock",
            headers=auth(setup["token"]),
            params={"item_id": setup["item"]["id"]},
        ).json()
        assert sum(Decimal(r["quantity"]) for r in rows) == Decimal("6.0000")

        accounts = client.get("/api/v1/accounts", headers=auth(setup["token"])).json()
        cogs = next(a for a in accounts if a["code"] == "5100")
        balance = client.get(
            f"/api/v1/accounts/{cogs['id']}/balance", headers=auth(setup["token"])
        ).json()["balance"]
        # 4 sold at a cost of 600 each.
        assert Decimal(str(balance)) == Decimal("2400.00")

    def test_the_books_stay_balanced_after_a_sale(self, client, setup):
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "شراء",
                    "quantity": "10",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع",
                    "quantity": "4",
                    "unit_price": "1000",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        trial = client.get(
            "/api/v1/reports/trial-balance", headers=auth(setup["token"])
        ).json()
        debits = sum(Decimal(str(r["debit"])) for r in trial["rows"])
        credits = sum(Decimal(str(r["credit"])) for r in trial["rows"])
        assert debits == credits

    def test_sale_cost_is_frozen_and_survives_a_later_price_change(self, client, setup):
        """Repricing stock must never restate a profit already reported."""
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "شراء أول",
                    "quantity": "5",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        sale = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع",
                    "quantity": "5",
                    "unit_price": "1000",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        cost_before = Decimal(str(sale["lines"][0]["cost_amount"]))
        assert cost_before == Decimal("3000.00")

        # Buy the same goods again at a much higher price.
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "شراء أغلى",
                    "quantity": "5",
                    "unit_price": "900",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        after = client.get(
            f"/api/v1/invoices/{sale['id']}", headers=auth(setup["token"])
        ).json()
        assert Decimal(str(after["lines"][0]["cost_amount"])) == cost_before


class TestProfitByOrigin:
    def test_a_sale_of_bought_goods_reports_purchased_cost(self, client, setup):
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "شراء",
                    "quantity": "10",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        sale = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع",
                    "quantity": "2",
                    "unit_price": "1000",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        line = sale["lines"][0]
        assert Decimal(str(line["cost_purchased"])) == Decimal("1200.00")
        assert Decimal(str(line["cost_manufactured"])) == Decimal("0.00")

    def test_a_sale_of_manufactured_goods_reports_manufactured_cost(self, client, setup):
        """Goods that came off a work order carry a manufactured cost."""
        item = setup["item"]
        seed_layer(
            item["id"],
            setup["finished"]["id"],
            quantity=10,
            unit_cost=450,
            source_type="production_receipt",
        )

        sale = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع إنتاج",
                    "quantity": "2",
                    "unit_price": "1000",
                    "item_id": item["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        line = sale["lines"][0]
        assert Decimal(str(line["cost_manufactured"])) == Decimal("900.00")
        assert Decimal(str(line["cost_purchased"])) == Decimal("0.00")

    def test_one_invoice_can_mix_both_and_split_the_cost(self, client, setup):
        """The whole reason origin lives on the layer, not on the invoice."""
        item = setup["item"]
        seed_layer(
            item["id"], setup["finished"]["id"], quantity=3, unit_cost=450,
            source_type="production_receipt", received_at="2026-11-01",
        )
        seed_layer(
            item["id"], setup["finished"]["id"], quantity=3, unit_cost=600,
            source_type="receipt", received_at="2026-11-02",
        )

        # Selling 5 consumes all 3 made ones then 2 bought ones.
        sale = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع مختلط",
                    "quantity": "5",
                    "unit_price": "1000",
                    "item_id": item["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        line = sale["lines"][0]
        assert Decimal(str(line["cost_manufactured"])) == Decimal("1350.00")  # 3 x 450
        assert Decimal(str(line["cost_purchased"])) == Decimal("1200.00")  # 2 x 600
        assert Decimal(str(line["cost_amount"])) == Decimal("2550.00")

        report = client.get(
            "/api/v1/reports/profit-by-source", headers=auth(setup["token"])
        ).json()
        assert Decimal(str(report["manufactured"]["revenue"])) == Decimal("3000.00")
        assert Decimal(str(report["manufactured"]["cost"])) == Decimal("1350.00")
        assert Decimal(str(report["manufactured"]["profit"])) == Decimal("1650.00")
        assert Decimal(str(report["purchased"]["revenue"])) == Decimal("2000.00")
        assert Decimal(str(report["purchased"]["cost"])) == Decimal("1200.00")
        assert Decimal(str(report["purchased"]["profit"])) == Decimal("800.00")


class TestFreeTextLines:
    def test_a_line_without_an_item_touches_no_stock(self, client, setup):
        """A service or a charge must not be forced through inventory."""
        invoice = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[{"description": "خدمة تركيب", "quantity": "1", "unit_price": "500"}],
        )
        assert Decimal(str(invoice["lines"][0]["cost_amount"])) == Decimal("0.00")
        rows = client.get("/api/v1/inventory/stock", headers=auth(setup["token"])).json()
        assert rows == []


class TestVoidingRestoresStock:
    def test_voiding_a_sale_returns_the_goods(self, client, setup):
        make_invoice(
            client,
            setup,
            kind="purchase",
            contact=setup["supplier"],
            lines=[
                {
                    "description": "شراء",
                    "quantity": "10",
                    "unit_price": "600",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        sale = make_invoice(
            client,
            setup,
            kind="sales",
            contact=setup["customer"],
            lines=[
                {
                    "description": "بيع",
                    "quantity": "4",
                    "unit_price": "1000",
                    "item_id": setup["item"]["id"],
                    "warehouse_id": setup["finished"]["id"],
                }
            ],
        )
        response = client.post(
            f"/api/v1/invoices/{sale['id']}/void",
            headers=auth(setup["token"]),
            json={"reason": "إلغاء"},
        )
        assert response.status_code == 200, response.text

        rows = client.get(
            "/api/v1/inventory/stock",
            headers=auth(setup["token"]),
            params={"item_id": setup["item"]["id"]},
        ).json()
        assert sum(Decimal(r["quantity"]) for r in rows) == Decimal("10.0000")


class TestOversellingIsRefused:
    def test_selling_more_than_stock_is_rejected(self, client, setup):
        response = client.post(
            "/api/v1/invoices",
            headers=auth(setup["token"]),
            json={
                "kind": "sales",
                "contact_id": setup["customer"]["id"],
                "issue_date": "2026-11-10",
                "lines": [
                    {
                        "description": "بيع بدون رصيد",
                        "quantity": "5",
                        "unit_price": "1000",
                        "item_id": setup["item"]["id"],
                        "warehouse_id": setup["finished"]["id"],
                    }
                ],
            },
        )
        assert response.status_code in (409, 422)
        assert "الرصيد" in response.text
