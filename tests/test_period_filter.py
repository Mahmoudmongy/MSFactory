"""Filtering invoice and payment lists by a date range."""
from __future__ import annotations

import pytest

from tests.test_api import auth, register


@pytest.fixture
def ctx(client):
    token = register(client, email="period@acme.eg", company="شركة الفترات")
    customer = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "عميل", "kind": "customer"}
    ).json()
    return {"token": token, "customer": customer}


def make_invoice(client, ctx, issue_date):
    response = client.post(
        "/api/v1/invoices",
        headers=auth(ctx["token"]),
        json={
            "kind": "sales",
            "contact_id": ctx["customer"]["id"],
            "issue_date": issue_date,
            "lines": [{"description": "بند", "quantity": "1", "unit_price": "100"}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestInvoicePeriodFilter:
    def test_start_and_end_narrow_the_list(self, client, ctx):
        make_invoice(client, ctx, "2026-01-05")
        inside = make_invoice(client, ctx, "2026-02-10")
        make_invoice(client, ctx, "2026-03-20")

        response = client.get(
            "/api/v1/invoices?start=2026-02-01&end=2026-02-28", headers=auth(ctx["token"])
        )
        assert response.status_code == 200
        numbers = [i["number"] for i in response.json()]
        assert numbers == [inside["number"]]

    def test_the_range_is_inclusive_at_both_ends(self, client, ctx):
        """A one-day range must find the invoice issued on that day."""
        invoice = make_invoice(client, ctx, "2026-02-10")
        response = client.get(
            "/api/v1/invoices?start=2026-02-10&end=2026-02-10", headers=auth(ctx["token"])
        )
        assert [i["number"] for i in response.json()] == [invoice["number"]]

    def test_no_filter_returns_everything(self, client, ctx):
        make_invoice(client, ctx, "2026-01-05")
        make_invoice(client, ctx, "2026-03-20")
        response = client.get("/api/v1/invoices", headers=auth(ctx["token"]))
        assert len(response.json()) == 2

    def test_one_bound_alone_is_applied(self, client, ctx):
        make_invoice(client, ctx, "2026-01-05")
        late = make_invoice(client, ctx, "2026-03-20")
        response = client.get("/api/v1/invoices?start=2026-02-01", headers=auth(ctx["token"]))
        assert [i["number"] for i in response.json()] == [late["number"]]


class TestPaymentPeriodFilter:
    def test_start_and_end_narrow_the_list(self, client, ctx):
        token = ctx["token"]
        account = next(
            a for a in client.get("/api/v1/accounts", headers=auth(token)).json() if a["code"] == "1010"
        )

        def pay(on):
            response = client.post(
                "/api/v1/payments",
                headers=auth(token),
                json={
                    "kind": "receive",
                    "contact_id": ctx["customer"]["id"],
                    "date": on,
                    "amount": "50",
                    "bank_account_id": account["id"],
                },
            )
            assert response.status_code == 201, response.text
            return response.json()

        pay("2026-01-05")
        inside = pay("2026-02-10")
        pay("2026-03-20")

        response = client.get(
            "/api/v1/payments?start=2026-02-01&end=2026-02-28", headers=auth(token)
        )
        assert response.status_code == 200
        assert [p["number"] for p in response.json()] == [inside["number"]]


class TestExport:
    def test_invoice_export_returns_a_workbook(self, client, ctx):
        make_invoice(client, ctx, "2026-02-10")
        response = client.get("/api/v1/invoices/export.xlsx", headers=auth(ctx["token"]))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert response.content[:2] == b"PK"
        assert "attachment" in response.headers["content-disposition"]

    def test_invoice_export_honours_the_period(self, client, ctx):
        from io import BytesIO

        from openpyxl import load_workbook

        make_invoice(client, ctx, "2026-01-05")
        inside = make_invoice(client, ctx, "2026-02-10")
        response = client.get(
            "/api/v1/invoices/export.xlsx?start=2026-02-01&end=2026-02-28",
            headers=auth(ctx["token"]),
        )
        workbook = load_workbook(BytesIO(response.content))
        sheet = next(s for s in workbook.worksheets if s.title.lower() == "invoices")
        numbers = [
            cell.value
            for row in sheet.iter_rows(min_row=2)
            for cell in row[:1]
            if cell.value
        ]
        assert numbers == [inside["number"]]

    def test_payment_export_returns_a_workbook(self, client, ctx):
        token = ctx["token"]
        account = next(
            a for a in client.get("/api/v1/accounts", headers=auth(token)).json() if a["code"] == "1010"
        )
        client.post(
            "/api/v1/payments",
            headers=auth(token),
            json={
                "kind": "receive",
                "contact_id": ctx["customer"]["id"],
                "date": "2026-02-10",
                "amount": "50",
                "bank_account_id": account["id"],
            },
        )
        response = client.get("/api/v1/payments/export.xlsx", headers=auth(token))
        assert response.status_code == 200
        assert response.content[:2] == b"PK"
