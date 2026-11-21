"""The custom (pivot) report.

Two things matter here. First, safety: the client asked for something Excel-like,
and the whole point of this design is that a user cannot name an arbitrary column
or expression. So the tests try to break the whitelist and assert a clean refusal
rather than a query. Second, arithmetic: a pivot that disagrees with the invoices
it summarises is worthless, so the totals are checked against known figures.
"""
from __future__ import annotations

import pytest

from tests.test_api import auth, register


def make_contact(client, token, name, kind):
    response = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": name, "kind": kind}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def token(client):
    return register(client, email="pivot@acme.eg", company="شركة التقارير")


@pytest.fixture
def seeded(client, token):
    """Two customers, one supplier, a purchase and two sales.

    Figures are chosen so each measure is distinct: 4 units bought at 250
    (cost 1000), 3 units sold at 1000 (net 3000, cost 750, profit 2250).
    """
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouse = next(
        w for w in client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
        if w["code"] == "FG"
    )
    item = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={"name": "منتج", "item_type": "purchased", "base_unit_id": piece["id"], "sale_price": "1000"},
    ).json()
    alice = make_contact(client, token, "أليس", "customer")
    bob = make_contact(client, token, "بوب", "customer")
    supplier = make_contact(client, token, "المورد", "supplier")

    def invoice(kind, contact, quantity, price, date="2026-03-05"):
        response = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": kind,
                "contact_id": contact["id"],
                "issue_date": date,
                "lines": [
                    {
                        "description": "بند",
                        "quantity": quantity,
                        "unit_price": price,
                        "tax_rate": "0",
                        "item_id": item["id"],
                        "warehouse_id": warehouse["id"],
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    invoice("purchase", supplier, "4", "250")
    invoice("sales", alice, "1", "1000")
    invoice("sales", bob, "2", "1000")
    return {"item": item, "alice": alice, "bob": bob}


def run(client, token, **params):
    response = client.get("/api/v1/reports/custom", headers=auth(token), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def total_of(report, column):
    """The grand-total row's value for one column."""
    total_row = next(row for row in report["rows"] if row.get("party") == "الإجمالي")
    return total_row[column]


class TestSafety:
    def test_an_unknown_dimension_is_refused(self, client, token, seeded):
        response = client.get(
            "/api/v1/reports/custom",
            headers=auth(token),
            params={"rows": "party;DROP TABLE invoices--"},
        )
        assert response.status_code == 422

    def test_an_unknown_measure_is_refused(self, client, token, seeded):
        response = client.get(
            "/api/v1/reports/custom", headers=auth(token), params={"values": "password"}
        )
        assert response.status_code == 422

    def test_an_unknown_kind_is_refused(self, client, token, seeded):
        response = client.get(
            "/api/v1/reports/custom", headers=auth(token), params={"kind": "refund"}
        )
        assert response.status_code == 422

    def test_a_refused_request_does_not_damage_the_data(self, client, token, seeded):
        client.get("/api/v1/reports/custom", headers=auth(token), params={"rows": "nope"})
        # The invoices are still there and still readable.
        assert client.get("/api/v1/invoices", headers=auth(token)).json()

    def test_the_pivot_is_scoped_to_the_company(self, client, token, seeded):
        other = register(client, email="other-pivot@acme.eg", company="شركة أخرى")
        # A company with no invoices gets no rows at all — not another
        # company's rows, and not a zero total implying it has data.
        assert run(client, other, rows="party", values="net")["rows"] == []


class TestArithmetic:
    def test_sales_net_totals_across_parties(self, client, token, seeded):
        report = run(client, token, rows="party", values="net", kind="sales")
        assert total_of(report, "net") == 3000

    def test_profit_is_revenue_minus_cost(self, client, token, seeded):
        report = run(client, token, rows="party", values="profit", kind="sales")
        assert total_of(report, "profit") == 2250

    def test_cost_matches_the_purchase_price_of_units_sold(self, client, token, seeded):
        report = run(client, token, rows="party", values="cost", kind="sales")
        assert total_of(report, "cost") == 750

    def test_quantity_sums_the_sold_units(self, client, token, seeded):
        report = run(client, token, rows="party", values="quantity", kind="sales")
        assert total_of(report, "quantity") == 3

    def test_a_second_dimension_splits_the_rows(self, client, token, seeded):
        report = run(client, token, rows="party,kind", values="net")
        sales = next(r for r in report["rows"] if r.get("party") == "أليس" and r.get("kind") == "مبيعات")
        purchase = next(r for r in report["rows"] if r.get("kind") == "مشتريات")
        assert sales["net"] == 1000
        assert purchase["net"] == 1000

    def test_a_column_dimension_crosses_the_measures(self, client, token, seeded):
        report = run(client, token, rows="party", columns="kind", values="net")
        assert "net مبيعات" in report["rows"][0]
        assert "net مشتريات" in report["rows"][0]

    def test_several_measures_can_be_read_at_once(self, client, token, seeded):
        report = run(client, token, rows="party", values="net,profit,cost", kind="sales")
        assert total_of(report, "net") == 3000
        assert total_of(report, "profit") == 2250
        assert total_of(report, "cost") == 750

    def test_a_date_filter_excludes_other_periods(self, client, token, seeded):
        inside = run(client, token, rows="party", values="net", start="2026-03-01", end="2026-03-31")
        outside = run(client, token, rows="party", values="net", start="2020-01-01", end="2020-01-31")
        assert total_of(inside, "net") == 4000
        assert outside["rows"] == []

    def test_the_report_names_its_own_shape(self, client, token, seeded):
        report = run(client, token, rows="month", columns="kind", values="net")
        assert [d["key"] for d in report["_row_dimensions"]] == ["month"]
        assert [d["key"] for d in report["_column_dimensions"]] == ["kind"]
        assert [m["key"] for m in report["_measures"]] == ["net"]

    def test_voided_invoices_are_not_counted(self, client, token, seeded):
        invoices = client.get("/api/v1/invoices", headers=auth(token)).json()
        sale = next(i for i in invoices if i["kind"] == "sales")
        voided = client.post(
            f"/api/v1/invoices/{sale['id']}/void", headers=auth(token), json={"reason": "خطأ"}
        )
        assert voided.status_code == 200, voided.text
        report = run(client, token, rows="party", values="net", kind="sales")
        # The list order is not guaranteed, so the expectation follows whichever
        # invoice was actually voided rather than assuming a particular one.
        assert total_of(report, "net") == 3000 - float(sale["subtotal"])


class TestCatalogueAndExport:
    def test_the_pivot_is_in_the_catalogue(self, client, token):
        catalog = client.get("/api/v1/reports/catalog", headers=auth(token)).json()
        keys = {r["key"] for category in catalog["categories"] for r in category["reports"]}
        assert "custom" in keys
        assert catalog["count"] == 30

    def test_the_pivot_exports_to_excel(self, client, token, seeded):
        import io

        from openpyxl import load_workbook

        response = client.get(
            "/api/v1/reports/custom/export.xlsx",
            headers=auth(token),
            params={"rows": "party", "values": "net", "kind": "sales"},
        )
        assert response.status_code == 200, response.text
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook[workbook.sheetnames[0]]
        values = [sheet.cell(row=row, column=2).value for row in range(1, sheet.max_row + 1)]
        assert 3000 in values
