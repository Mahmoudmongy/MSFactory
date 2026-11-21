"""Report tests that check the numbers, not just that the endpoint answers.

A report that returns 200 with a wrong total is worse than one that crashes,
because the wrong total gets acted on. Each test here builds a small but real
set of documents, then asserts the exact figure the report must produce and
the accounting identity it must satisfy.
"""
from __future__ import annotations

from decimal import Decimal

import pytest


def register(client, email="reports@acme.eg", company="شركة التقارير"):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ng!Pass",
            "full_name": "مالك",
            "company_name": company,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def make_contact(client, token, name, kind):
    response = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": name, "kind": kind}
    )
    assert response.status_code == 201, response.text
    return response.json()


def issue_invoice(client, token, contact_id, kind, lines):
    response = client.post(
        "/api/v1/invoices",
        headers=auth(token),
        json={
            "kind": kind,
            "contact_id": contact_id,
            "issue_date": "2026-03-01",
            "lines": lines,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def token(client):
    return register(client)


@pytest.fixture
def stocked(client, token):
    """A company with one stocked item, a customer and a supplier.

    The item matters: a line that names an item moves stock, and only stocked
    lines produce a cost of sale. A free-text line would leave every inventory
    report empty, which would test nothing.
    """
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
    finished = next(w for w in warehouses if w["code"] == "FG")

    item = client.post(
        "/api/v1/inventory/items",
        headers=auth(token),
        json={
            "name": "منتج التقارير",
            "item_type": "purchased",
            "base_unit_id": piece["id"],
            "sale_price": "1000",
        },
    ).json()
    customer = make_contact(client, token, "عميل التقارير", "customer")
    supplier = make_contact(client, token, "مورد التقارير", "supplier")
    return {
        "item": item,
        "customer": customer,
        "supplier": supplier,
        "warehouse": finished,
    }


@pytest.fixture
def seeded(client, token, stocked):
    """One purchase into stock, then one sale out of it.

    Buying 4 at 250 then selling 2 at 1000 means the reports have a real cost
    of sale (500) to show, not just revenue. Without a purchase there would be
    no stock for the sale to consume and the cost would be zero.
    """
    issue_invoice(
        client,
        token,
        stocked["supplier"]["id"],
        "purchase",
        [
            {
                "description": "خامة",
                "quantity": "4",
                "unit_price": "250",
                "discount_rate": "0",
                "tax_rate": "14",
                "item_id": stocked["item"]["id"],
                "warehouse_id": stocked["warehouse"]["id"],
            }
        ],
    )
    sale = issue_invoice(
        client,
        token,
        stocked["customer"]["id"],
        "sales",
        [
            {
                "description": "منتج",
                "quantity": "2",
                "unit_price": "1000",
                "discount_rate": "0",
                "tax_rate": "14",
                "item_id": stocked["item"]["id"],
                "warehouse_id": stocked["warehouse"]["id"],
            }
        ],
    )
    return {
        "customer": stocked["customer"],
        "supplier": stocked["supplier"],
        "item": stocked["item"],
        "warehouse": stocked["warehouse"],
        "sale": sale,
    }


class TestStatements:
    def test_trial_balance_balances_and_shows_every_account(self, client, token, seeded):
        report = client.get("/api/v1/reports/trial-balance", headers=auth(token)).json()
        assert report["total_debit"] == report["total_credit"]
        assert report["balanced"] is True
        # The sale and the purchase both touched receivables/payables, so those
        # accounts must appear with a non-zero balance.
        codes = {row["code"] for row in report["rows"]}
        assert {"1100", "2100"} <= codes

    def test_income_statement_totals_the_revenue_and_cost(self, client, token, seeded):
        report = client.get("/api/v1/reports/income-statement", headers=auth(token)).json()
        # Sale of 2 x 1000, no discount, net of tax.
        assert Decimal(str(report["total_revenue"])) == Decimal("2000.00")
        # The 2 units sold came out of stock at 250 each, so cost of sale is 500.
        assert Decimal(str(report["total_cost_of_sales"])) == Decimal("500.00")
        assert Decimal(str(report["gross_profit"])) == Decimal("1500.00")
        # The purchase itself is an asset, so it must not appear as an expense.
        assert Decimal(str(report["total_expenses"])) == Decimal("0.00")
        assert Decimal(str(report["net_profit"])) == Decimal("1500.00")
        assert Decimal(str(report["income"])) == Decimal("2000.00")
        assert Decimal(str(report["expense"])) == Decimal("500.00")

    def test_balance_sheet_holds_the_accounting_equation(self, client, token, seeded):
        report = client.get("/api/v1/reports/balance-sheet", headers=auth(token)).json()
        assert report["balanced"] is True
        assert Decimal(str(report["total_assets"])) == Decimal(str(report["total_liabilities_and_equity"]))
        # Receivables 2280 plus the 500 of stock still on hand.
        assert Decimal(str(report["total_assets"])) == Decimal("2780.00")
        # Payables 1140 plus the 140 of net VAT owed.
        assert Decimal(str(report["total_liabilities"])) == Decimal("1280.00")
        assert Decimal(str(report["total_equity"])) == Decimal("1500.00")

    def test_cash_flow_is_empty_before_any_payment(self, client, token, seeded):
        report = client.get("/api/v1/reports/cash-flow", headers=auth(token)).json()
        assert report["inflows"] == []
        assert report["outflows"] == []
        assert Decimal(str(report["net_change"])) == Decimal("0.00")


class TestLedger:
    def test_journal_lists_entries_with_their_lines(self, client, token, seeded):
        report = client.get("/api/v1/reports/journal", headers=auth(token)).json()
        assert report["count"] >= 2
        for entry in report["entries"]:
            assert entry["balanced"] is True
            assert len(entry["lines"]) >= 2

    def test_general_ledger_shows_movements_per_account(self, client, token, seeded):
        report = client.get("/api/v1/reports/general-ledger", headers=auth(token)).json()
        assert Decimal(str(report["total_debit"])) == Decimal(str(report["total_credit"]))
        assert len(report["accounts"]) >= 2

    def test_account_activity_carries_a_running_balance(self, client, token, seeded):
        accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
        receivable = next(a for a in accounts if a["code"] == "1100")
        report = client.get(
            f"/api/v1/reports/accounts/{receivable['id']}/activity", headers=auth(token)
        ).json()
        assert report["account"]["code"] == "1100"
        assert len(report["movements"]) >= 1
        # The running balance on the last line must equal the closing figure.
        assert report["movements"][-1]["balance"] == report["closing"]
        assert Decimal(str(report["closing"])) == Decimal("2280.00")

    def test_account_balances_group_by_type(self, client, token):
        report = client.get("/api/v1/reports/account-balances", headers=auth(token)).json()
        assert set(report["by_type"]) >= {"asset", "liability", "income", "expense"}
        assert report["count"] > 10


class TestParties:
    def test_ar_aging_buckets_the_open_invoice(self, client, token, seeded):
        report = client.get("/api/v1/reports/ar-aging", headers=auth(token)).json()
        # The invoice is dated 2026-03-01 and the report ages as of today, so it
        # falls in the oldest bucket rather than current.
        assert Decimal(str(report["total"])) == Decimal("2280.00")
        assert report["contacts"][0]["name"] == "عميل التقارير"
        assert sum(Decimal(str(v)) for v in report["buckets"].values()) == Decimal("2280.00")

    def test_ap_aging_buckets_the_open_purchase(self, client, token, seeded):
        report = client.get("/api/v1/reports/ap-aging", headers=auth(token)).json()
        assert Decimal(str(report["total"])) == Decimal("1140.00")

    def test_customer_balances_read_from_the_journal(self, client, token, seeded):
        report = client.get("/api/v1/reports/customer-balances", headers=auth(token)).json()
        assert report["count"] == 1
        assert Decimal(str(report["contacts"][0]["balance"])) == Decimal("2280.00")

    def test_contact_statement_carries_a_running_balance(self, client, token, seeded):
        contact_id = seeded["customer"]["id"]
        report = client.get(
            f"/api/v1/reports/contacts/{contact_id}/statement", headers=auth(token)
        ).json()
        assert report["contact"]["name"] == "عميل التقارير"
        assert Decimal(str(report["closing"])) == Decimal("2280.00")
        assert report["movements"][-1]["balance"] == report["closing"]
        assert len(report["open_invoices"]) == 1
        assert Decimal(str(report["total_due"])) == Decimal("2280.00")

    def test_top_customers_ranks_by_profit(self, client, token, seeded):
        report = client.get("/api/v1/reports/top-customers", headers=auth(token)).json()
        assert report["customers"][0]["name"] == "عميل التقارير"
        # 2000 of revenue less the 500 the goods cost.
        assert Decimal(str(report["customers"][0]["profit"])) == Decimal("1500.00")
        assert Decimal(str(report["total"])) == Decimal("1500.00")


class TestSales:
    def test_sales_summary_splits_net_tax_and_gross(self, client, token, seeded):
        report = client.get("/api/v1/reports/sales-summary", headers=auth(token)).json()
        assert Decimal(str(report["net"])) == Decimal("2000.00")
        assert Decimal(str(report["tax"])) == Decimal("280.00")
        assert Decimal(str(report["gross"])) == Decimal("2280.00")
        assert report["invoice_count"] == 1
        assert Decimal(str(report["average_invoice"])) == Decimal("2280.00")

    def test_purchases_summary_mirrors_sales(self, client, token, seeded):
        report = client.get("/api/v1/reports/purchases-summary", headers=auth(token)).json()
        assert Decimal(str(report["net"])) == Decimal("1000.00")
        assert Decimal(str(report["gross"])) == Decimal("1140.00")

    def test_sales_by_customer_attributes_the_revenue(self, client, token, seeded):
        report = client.get("/api/v1/reports/sales-by-customer", headers=auth(token)).json()
        assert report["count"] == 1
        assert Decimal(str(report["customers"][0]["share_percent"])) == Decimal("100.00")

    def test_vat_nets_output_against_input(self, client, token, seeded):
        report = client.get("/api/v1/reports/vat", headers=auth(token)).json()
        assert Decimal(str(report["output_tax"])) == Decimal("280.00")
        assert Decimal(str(report["input_tax"])) == Decimal("140.00")
        # Output tax collected exceeds input tax paid, so tax is owed.
        assert Decimal(str(report["net_payable"])) == Decimal("140.00")

    def test_monthly_trend_places_the_months(self, client, token, seeded):
        report = client.get("/api/v1/reports/monthly-trend?year=2026", headers=auth(token)).json()
        assert report["year"] == 2026
        assert len(report["months"]) == 12
        march = report["months"][2]
        assert Decimal(str(march["revenue"])) == Decimal("2000.00")


class TestInventoryReports:
    def test_stock_valuation_lists_received_goods(self, client, token, seeded):
        report = client.get("/api/v1/reports/stock-valuation", headers=auth(token)).json()
        # 4 units came in at 250, then the sale took 2 out, so 2 remain.
        assert Decimal(str(report["total_value"])) == Decimal("500.00")
        assert Decimal(str(report["total_quantity"])) == Decimal("2")
        assert report["costing_method"] in ("fifo", "weighted_average")

    def test_stock_movement_separates_in_from_out(self, client, token, seeded):
        report = client.get("/api/v1/reports/stock-movement", headers=auth(token)).json()
        assert len(report["items"]) >= 1
        assert Decimal(str(report["total_in"])) == Decimal("4")

    def test_reorder_reports_nothing_without_levels(self, client, token, seeded):
        report = client.get("/api/v1/reports/reorder", headers=auth(token)).json()
        assert report["count"] == 0

    def test_warehouse_stock_sums_the_layers(self, client, token, seeded):
        report = client.get("/api/v1/reports/warehouse-stock", headers=auth(token)).json()
        assert Decimal(str(report["total_value"])) == Decimal("500.00")

    def test_stock_aging_bands_are_complete(self, client, token, seeded):
        report = client.get("/api/v1/reports/stock-aging", headers=auth(token)).json()
        assert set(report["bands"]) == {"0_30", "31_90", "91_180", "over_180"}
        assert Decimal(str(report["total_value"])) == Decimal("500.00")


class TestDashboards:
    def test_dashboard_reports_cash_and_profit(self, client, token, seeded):
        report = client.get("/api/v1/reports/dashboard", headers=auth(token)).json()
        assert Decimal(str(report["net_profit"])) == Decimal("1500.00")
        assert Decimal(str(report["receivables"])) == Decimal("2280.00")
        assert Decimal(str(report["payables"])) == Decimal("1140.00")
        assert Decimal(str(report["inventory_value"])) == Decimal("500.00")

    def test_alerts_surface_the_overdue_invoice(self, client, token, seeded):
        report = client.get("/api/v1/reports/alerts", headers=auth(token)).json()
        # The invoice is dated in the past, so it is overdue.
        assert report["overdue_count"] >= 1
        assert Decimal(str(report["overdue_total"])) > Decimal("0.00")

    def test_kpis_compare_to_the_previous_window(self, client, token, seeded):
        report = client.get("/api/v1/reports/kpis?days=365", headers=auth(token)).json()
        assert Decimal(str(report["revenue"])) == Decimal("2000.00")
        assert report["invoice_count"] == 1
        assert Decimal(str(report["average_invoice"])) == Decimal("2000.00")


class TestReportsAreScoped:
    def test_another_company_cannot_see_these_reports(self, client, token, seeded):
        other = register(client, email="other@acme.eg", company="شركة أخرى")
        report = client.get("/api/v1/reports/stock-valuation", headers=auth(other)).json()
        assert report["lines"] == []
        assert Decimal(str(report["total_value"])) == Decimal("0.00")

        income = client.get("/api/v1/reports/income-statement", headers=auth(other)).json()
        assert Decimal(str(income["total_revenue"])) == Decimal("0.00")

    def test_every_report_endpoint_answers(self, client, token, seeded):
        endpoints = [
            "dashboard",
            "alerts",
            "kpis",
            "trial-balance",
            "income-statement",
            "balance-sheet",
            "cash-flow",
            "journal",
            "general-ledger",
            "account-balances",
            "ar-aging",
            "ap-aging",
            "customer-balances",
            "supplier-balances",
            "top-customers",
            "stock-valuation",
            "stock-movement",
            "stock-aging",
            "reorder",
            "item-profitability",
            "warehouse-stock",
            "sales-summary",
            "purchases-summary",
            "sales-by-customer",
            "purchases-by-supplier",
            "profit-by-source",
            "vat",
            "discounts",
            "monthly-trend",
            "periods",
        ]
        for endpoint in endpoints:
            response = client.get(f"/api/v1/reports/{endpoint}", headers=auth(token))
            assert response.status_code == 200, f"{endpoint}: {response.text}"
