"""Excel export and report catalogue tests.

The export is the feature the client asked for by name, so it is checked the
way a person would use it: the file must be a real .xlsx that a spreadsheet
application opens, and the figures in it must be the same figures the screen
shows. A download that produces a corrupt file, or a file whose totals differ
from the report, is the failure this guards against.
"""
from __future__ import annotations

import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from tests.test_api import auth, register

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def make_contact(client, token, name, kind):
    response = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": name, "kind": kind}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def token(client):
    return register(client, email="excel@acme.eg", company="شركة التصدير")


@pytest.fixture
def seeded(client, token):
    """A stocked item, a purchase into it, then a sale out of it."""
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
    customer = make_contact(client, token, "عميل", "customer")
    supplier = make_contact(client, token, "مورد", "supplier")

    for kind, contact, quantity, price in (
        ("purchase", supplier, "4", "250"),
        ("sales", customer, "2", "1000"),
    ):
        response = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": kind,
                "contact_id": contact["id"],
                "issue_date": "2026-03-01",
                "lines": [
                    {
                        "description": "بند",
                        "quantity": quantity,
                        "unit_price": price,
                        "tax_rate": "14",
                        "item_id": item["id"],
                        "warehouse_id": warehouse["id"],
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
    return {"item": item, "customer": customer}


class TestCatalogue:
    def test_catalog_lists_reports_by_category(self, client, token):
        catalog = client.get("/api/v1/reports/catalog", headers=auth(token)).json()
        assert catalog["count"] == 30
        keys = {r["key"] for category in catalog["categories"] for r in category["reports"]}
        assert {"dashboard", "income-statement", "balance-sheet", "vat", "stock-valuation"} <= keys

    def test_every_catalogued_report_actually_runs(self, client, token, seeded):
        catalog = client.get("/api/v1/reports/catalog", headers=auth(token)).json()
        for category in catalog["categories"]:
            for report in category["reports"]:
                response = client.get(f"/api/v1/reports/{report['key']}", headers=auth(token))
                assert response.status_code == 200, f"{report['key']}: {response.text}"

    def test_an_unknown_report_is_a_clear_404(self, client, token):
        assert client.get("/api/v1/reports/does-not-exist", headers=auth(token)).status_code == 404


class TestExcelExport:
    def _download(self, client, token, key, **params):
        response = client.get(f"/api/v1/reports/{key}/export.xlsx", headers=auth(token), params=params)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == XLSX_TYPE
        return response

    def test_export_is_a_readable_workbook(self, client, token, seeded):
        response = self._download(client, token, "trial-balance")
        # A zip magic number: an .xlsx is a zip. A wrong Content-Length or a
        # half-written stream would show up here as a corrupt file.
        assert response.content[:2] == b"PK"
        workbook = load_workbook(io.BytesIO(response.content))
        assert workbook.sheetnames

    def test_export_filename_is_an_xlsx_and_arabic_safe(self, client, token, seeded):
        response = self._download(client, token, "income-statement")
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert ".xlsx" in disposition
        # Percent-encoded, so a raw Arabic filename never breaks the header.
        assert "filename*=UTF-8''" in disposition

    def test_exported_totals_match_the_report(self, client, token, seeded):
        """The number in the sheet must equal the number on screen."""
        report = client.get("/api/v1/reports/income-statement", headers=auth(token)).json()
        response = self._download(client, token, "income-statement")
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook[workbook.sheetnames[0]]

        labels = {
            str(sheet.cell(row=row, column=1).value): sheet.cell(row=row, column=2).value
            for row in range(1, sheet.max_row + 1)
        }
        # The workbook is Arabic, like the screen it came from.
        assert labels.get("إجمالي الإيرادات") == float(report["total_revenue"])
        assert labels.get("صافي الربح") == float(report["net_profit"])

    def test_numbers_are_exported_as_numbers(self, client, token, seeded):
        """Text-formatted figures cannot be summed, which defeats the export."""
        response = self._download(client, token, "income-statement")
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook[workbook.sheetnames[0]]
        revenue_cell = next(
            sheet.cell(row=row, column=2)
            for row in range(1, sheet.max_row + 1)
            if sheet.cell(row=row, column=1).value == "إجمالي الإيرادات"
        )
        assert isinstance(revenue_cell.value, (int, float))
        assert revenue_cell.number_format == "#,##0.00"

    def test_a_list_report_becomes_its_own_sheet(self, client, token, seeded):
        response = self._download(client, token, "stock-valuation")
        workbook = load_workbook(io.BytesIO(response.content))
        # The valuation lines get their own tab, separate from the summary.
        assert any("البنود" in name for name in workbook.sheetnames)
        lines = workbook[next(name for name in workbook.sheetnames if "البنود" in name)]
        headers = [lines.cell(row=1, column=column).value for column in range(1, lines.max_column + 1)]
        assert "الكمية" in headers
        assert "التكلفة" in headers

    def test_sheets_are_right_to_left_for_arabic(self, client, token, seeded):
        response = self._download(client, token, "balance-sheet")
        workbook = load_workbook(io.BytesIO(response.content))
        assert all(workbook[name].sheet_view.rightToLeft for name in workbook.sheetnames)

    def test_every_catalogued_report_exports(self, client, token, seeded):
        catalog = client.get("/api/v1/reports/catalog", headers=auth(token)).json()
        for category in catalog["categories"]:
            for report in category["reports"]:
                response = self._download(client, token, report["key"])
                assert response.content[:2] == b"PK", f"{report['key']} is not a zip"
                load_workbook(io.BytesIO(response.content))

    def test_export_is_scoped_to_the_company(self, client, token, seeded):
        other = register(client, email="other-export@acme.eg", company="شركة أخرى")
        response = self._download(client, other, "stock-valuation")
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook[workbook.sheetnames[0]]
        labels = {
            str(sheet.cell(row=row, column=1).value): sheet.cell(row=row, column=2).value
            for row in range(1, sheet.max_row + 1)
        }
        assert labels.get("إجمالي القيمة") == 0

    def test_export_requires_authentication(self, client):
        assert client.get("/api/v1/reports/trial-balance/export.xlsx").status_code in (401, 403)

    def test_export_respects_a_date_filter(self, client, token, seeded):
        in_range = self._download(client, token, "sales-summary", start="2026-03-01", end="2026-03-31")
        out_of_range = self._download(client, token, "sales-summary", start="2020-01-01", end="2020-01-31")

        def net(response):
            sheet = load_workbook(io.BytesIO(response.content))[load_workbook(io.BytesIO(response.content)).sheetnames[0]]
            for row in range(1, sheet.max_row + 1):
                if sheet.cell(row=row, column=1).value == "الصافي":
                    return Decimal(str(sheet.cell(row=row, column=2).value))
            raise AssertionError("no net row")

        assert net(in_range) == Decimal("2000")
        assert net(out_of_range) == Decimal("0")
