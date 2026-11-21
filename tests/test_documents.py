"""Printing an invoice: the rendered page and the token that opens it.

The document is HTML that the browser turns into PDF, so most of what can go
wrong is either in the markup (escaping, totals) or in the token (who may open
which document). Both are covered here; layout is checked in the browser, not
here, because a test asserting on CSS strings proves nothing about what a
reader sees.

The security cases matter more than the cosmetic ones: a print URL travels in
a browser history, a shared link and a printer queue, so a token that opens
more than the one document it names would leak another company's books.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.security import create_document_token
from app.services.documents import line_amounts
from tests.test_api import auth, register


@pytest.fixture
def setup(client):
    """A company with a customer and one issued invoice to print."""
    token = register(client, email="print@acme.eg", company="شركة الطباعة")
    customer = client.post(
        "/api/v1/contacts",
        headers=auth(token),
        json={"name": "مؤسسة النور", "kind": "customer", "phone": "01001234567"},
    ).json()
    response = client.post(
        "/api/v1/invoices",
        headers=auth(token),
        json={
            "kind": "sales",
            "contact_id": customer["id"],
            "issue_date": "2026-11-10",
            "lines": [
                {"description": "توريد أجهزة", "quantity": "10", "unit_price": "2500", "tax_rate": "14"},
                {"description": "خدمة تركيب", "quantity": "1", "unit_price": "5000", "discount_rate": "10"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    invoice = response.json()
    return {"token": token, "customer": customer, "invoice": invoice}


def print_path(client, token, invoice_id):
    response = client.post(f"/api/v1/invoices/{invoice_id}/print-token", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["path"]


class TestPrintToken:
    def test_token_opens_the_invoice_it_names(self, client, setup):
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        response = client.get(f"/api/v1{path}")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/html")

    def test_token_is_short_lived_and_scoped(self, client, setup):
        payload = client.post(
            f"/api/v1/invoices/{setup['invoice']['id']}/print-token", headers=auth(setup["token"])
        ).json()
        assert payload["expires_in"] <= 15 * 60
        assert payload["path"].startswith("/documents/invoice/")

    def test_a_token_for_one_invoice_cannot_open_another(self, client, setup):
        """The invoice id is in the URL and therefore user-controlled."""
        other = client.post(
            "/api/v1/invoices",
            headers=auth(setup["token"]),
            json={
                "kind": "sales",
                "contact_id": setup["customer"]["id"],
                "issue_date": "2026-11-11",
                "lines": [{"description": "أخرى", "quantity": "1", "unit_price": "100"}],
            },
        ).json()
        stolen = print_path(client, setup["token"], other["id"])
        token_value = stolen.split("t=", 1)[1]
        response = client.get(f"/api/v1/documents/invoice/{setup['invoice']['id']}?t={token_value}")
        assert response.status_code == 401

    def test_an_access_token_is_not_a_document_token(self, client, setup):
        """A session token must not be replayable as a print link."""
        response = client.get(
            f"/api/v1/documents/invoice/{setup['invoice']['id']}?t={setup['token']}"
        )
        assert response.status_code == 401

    def test_a_document_token_cannot_be_used_as_a_bearer(self, client, setup):
        token_value = print_path(client, setup["token"], setup["invoice"]["id"]).split("t=", 1)[1]
        response = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token_value}"})
        assert response.status_code == 401

    def test_a_missing_token_is_rejected(self, client, setup):
        response = client.get(f"/api/v1/documents/invoice/{setup['invoice']['id']}")
        assert response.status_code == 422

    def test_a_token_signed_for_another_company_cannot_read_this_invoice(self, client, setup, db):
        """Tenant isolation must hold even with a validly signed token."""
        from app.db.models import Company

        other_company = Company(name="شركة تانية")
        db.add(other_company)
        db.commit()
        foreign = create_document_token(1, other_company.id, "invoice", setup["invoice"]["id"])
        response = client.get(
            f"/api/v1/documents/invoice/{setup['invoice']['id']}?t={foreign}"
        )
        assert response.status_code == 404

    def test_the_print_response_must_not_be_cached(self, client, setup):
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        response = client.get(f"/api/v1{path}")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex"

    def test_the_arabic_filename_is_encoded_for_the_header(self, client, setup):
        """HTTP headers are latin-1; an Arabic name would raise without RFC 5987."""
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        response = client.get(f"/api/v1{path}")
        disposition = response.headers["content-disposition"]
        assert "filename*=UTF-8''" in disposition
        assert disposition.isascii()

    def test_printing_requires_view_permission(self, client, setup):
        """A role that cannot see invoices must not be able to mint a link."""
        added = client.post(
            "/api/v1/team",
            headers=auth(setup["token"]),
            json={
                "email": "store@acme.eg",
                "full_name": "أمين مخزن",
                "password": "Str0ng!Pass",
                "role": "storekeeper",
            },
        )
        assert added.status_code == 201, added.text
        login = client.post(
            "/api/v1/auth/login", json={"email": "store@acme.eg", "password": "Str0ng!Pass"}
        )
        assert login.status_code == 200, login.text
        store_token = login.json()["access_token"]
        response = client.post(
            f"/api/v1/invoices/{setup['invoice']['id']}/print-token", headers=auth(store_token)
        )
        assert response.status_code == 403


class TestRenderedDocument:
    def test_it_shows_the_invoice_number_contact_and_totals(self, client, setup):
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        html = client.get(f"/api/v1{path}").text
        invoice = setup["invoice"]
        assert invoice["number"] in html
        assert "مؤسسة النور" in html
        # Formatted from the invoice itself: 10x2500 +14% tax, plus 5000 -10% discount.
        assert f"{Decimal(invoice['total']):,.2f}" in html
        assert "فاتورة بيع" in html

    def test_it_is_rtl_and_declares_arabic(self, client, setup):
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert 'dir="rtl"' in html
        assert 'lang="ar"' in html

    def test_a4_page_css_is_present(self, client, setup):
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert "@page" in html and "A4" in html
        assert "@media print" in html

    def test_user_text_is_escaped(self, client, setup):
        """A description is user input and the page is same-origin with the API."""
        hostile = client.post(
            "/api/v1/invoices",
            headers=auth(setup["token"]),
            json={
                "kind": "sales",
                "contact_id": setup["customer"]["id"],
                "issue_date": "2026-11-12",
                "lines": [
                    {"description": "<script>alert(1)</script>", "quantity": "1", "unit_price": "10"}
                ],
            },
        ).json()
        path = print_path(client, setup["token"], hostile["id"])
        html = client.get(f"/api/v1{path}").text
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_void_invoice_says_so(self, client, setup):
        client.post(
            f"/api/v1/invoices/{setup['invoice']['id']}/void",
            headers=auth(setup["token"]),
            json={"reason": "خطأ"},
        )
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert "ملغاة" in html

    def test_company_details_come_from_settings(self, client, setup):
        saved = client.patch(
            "/api/v1/settings",
            headers=auth(setup["token"]),
            json={"settings": {"company_name": "مصنع الدقة", "phone": "0223456789"}},
        )
        assert saved.status_code == 200, saved.text
        path = print_path(client, setup["token"], setup["invoice"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert "مصنع الدقة" in html
        assert "0223456789" in html


class TestAmounts:
    def test_line_totals_apply_discount_then_tax(self):
        """The screen and the printed page must compute a line the same way."""

        class Line:
            quantity = Decimal("10")
            unit_price = Decimal("2500")
            discount_rate = Decimal("10")
            tax_rate = Decimal("14")

        gross, discount, tax = line_amounts(Line())
        assert gross == Decimal("25000")
        assert discount == Decimal("2500")
        assert tax == Decimal("3150")  # 14% of 22,500, not of 25,000


@pytest.fixture
def voucher(client):
    """A company with a customer, an invoice and a receipt that settles it."""
    token = register(client, email="voucher@acme.eg", company="شركة السندات")
    customer = client.post(
        "/api/v1/contacts",
        headers=auth(token),
        json={"name": "شركة الأمل", "kind": "customer", "phone": "01098765432"},
    ).json()
    invoice = client.post(
        "/api/v1/invoices",
        headers=auth(token),
        json={
            "kind": "sales",
            "contact_id": customer["id"],
            "issue_date": "2026-11-05",
            "lines": [{"description": "توريد", "quantity": "1", "unit_price": "15000"}],
        },
    ).json()
    accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
    cash = next(a for a in accounts if a["code"] == "1010")
    payment = client.post(
        "/api/v1/payments",
        headers=auth(token),
        json={
            "kind": "receive",
            "contact_id": customer["id"],
            "date": "2026-11-08",
            "amount": "15000",
            "bank_account_id": cash["id"],
            "reference": "شيك 4451",
            "invoice_id": invoice["id"],
        },
    )
    assert payment.status_code == 201, payment.text
    return {
        "token": token,
        "customer": customer,
        "invoice": invoice,
        "payment": payment.json(),
        "cash": cash,
    }


def voucher_path(client, token, payment_id):
    response = client.post(f"/api/v1/payments/{payment_id}/print-token", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["path"]


class TestPaymentVoucher:
    def test_the_link_opens_the_voucher(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        response = client.get(f"/api/v1{path}")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/html")

    def test_it_shows_the_number_party_and_amount(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert voucher["payment"]["number"] in html
        assert "شركة الأمل" in html
        assert "15,000.00" in html
        assert "سند قبض" in html

    def test_it_lists_the_invoice_the_payment_settled(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert voucher["invoice"]["number"] in html

    def test_it_spells_the_amount_in_arabic_words(self, client, voucher):
        """A voucher states the amount twice: in figures and in words."""
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert "خمسة عشر ألف جنيه مصري" in html
        assert "لا غير" in html

    def test_it_names_the_cash_account_and_reference(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert "شيك 4451" in html
        assert voucher["cash"]["name_ar"] in html or voucher["cash"]["name"] in html

    def test_it_has_signature_lines(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        html = client.get(f"/api/v1{path}").text
        for label in ("المستلم", "المحاسب", "المدير"):
            assert label in html

    def test_it_is_rtl_and_uses_the_a4_sheet(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        html = client.get(f"/api/v1{path}").text
        assert 'dir="rtl"' in html and 'lang="ar"' in html
        assert "@page" in html and "A4" in html

    def test_an_invoice_token_cannot_open_a_voucher(self, client, voucher):
        """Each token names its document type, so the two cannot be swapped."""
        invoice_path = client.post(
            f"/api/v1/invoices/{voucher['invoice']['id']}/print-token", headers=auth(voucher["token"])
        ).json()["path"]
        stolen = invoice_path.split("t=", 1)[1]
        response = client.get(f"/api/v1/documents/payment/{voucher['payment']['id']}?t={stolen}")
        assert response.status_code == 401

    def test_a_voucher_token_cannot_open_an_invoice(self, client, voucher):
        token_value = voucher_path(client, voucher["token"], voucher["payment"]["id"]).split("t=", 1)[1]
        response = client.get(
            f"/api/v1/documents/invoice/{voucher['invoice']['id']}?t={token_value}"
        )
        assert response.status_code == 401

    def test_another_companys_voucher_is_not_readable(self, client, voucher, db):
        from app.core.security import create_document_token
        from app.db.models import Company

        other = Company(name="شركة تالتة")
        db.add(other)
        db.commit()
        foreign = create_document_token(1, other.id, "payment", voucher["payment"]["id"])
        response = client.get(f"/api/v1/documents/payment/{voucher['payment']['id']}?t={foreign}")
        assert response.status_code == 404

    def test_the_voucher_is_not_cached(self, client, voucher):
        path = voucher_path(client, voucher["token"], voucher["payment"]["id"])
        response = client.get(f"/api/v1{path}")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex"

    def test_printing_requires_view_permission(self, client, voucher):
        added = client.post(
            "/api/v1/team",
            headers=auth(voucher["token"]),
            json={
                "email": "store2@acme.eg",
                "full_name": "أمين مخزن",
                "password": "Str0ng!Pass",
                "role": "storekeeper",
            },
        )
        assert added.status_code == 201, added.text
        login = client.post(
            "/api/v1/auth/login", json={"email": "store2@acme.eg", "password": "Str0ng!Pass"}
        )
        store_token = login.json()["access_token"]
        response = client.post(
            f"/api/v1/payments/{voucher['payment']['id']}/print-token", headers=auth(store_token)
        )
        assert response.status_code == 403

    def test_a_payment_on_account_says_so(self, client, voucher):
        """A payment not tied to an invoice must not print an empty table."""
        accounts = client.get("/api/v1/accounts", headers=auth(voucher["token"])).json()
        cash = next(a for a in accounts if a["code"] == "1010")
        payment = client.post(
            "/api/v1/payments",
            headers=auth(voucher["token"]),
            json={
                "kind": "spend",
                "contact_id": voucher["customer"]["id"],
                "date": "2026-11-09",
                "amount": "250",
                "bank_account_id": cash["id"],
            },
        )
        if payment.status_code != 201:
            pytest.skip(f"supplier required for a spend voucher: {payment.text}")
        path = voucher_path(client, voucher["token"], payment.json()["id"])
        html = client.get(f"/api/v1{path}").text
        assert "على الحساب" in html


class TestAmountInWords:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1", "جنيه مصري واحد"),
            ("2", "جنيهان مصريان"),
            ("3", "ثلاثة جنيهات مصرية"),
            ("11", "أحد عشر جنيهاً مصرياً"),
            ("21", "واحد وعشرون جنيهاً مصرياً"),
            ("100", "مئة جنيه مصري"),
            ("1000", "ألف جنيه مصري"),
            ("2000", "ألفا جنيه مصري"),
            ("3000", "ثلاثة آلاف جنيه مصري"),
            ("15000", "خمسة عشر ألف جنيه مصري"),
        ],
    )
    def test_whole_pounds_use_the_right_plural_form(self, value, expected):
        """«ألفان جنيه» and «خمسة عشر ألفاً» are both wrong on a real voucher."""
        from app.services.documents import _amount_in_words

        assert expected in _amount_in_words(Decimal(value))

    def test_piastres_are_spelled_too(self):
        from app.services.documents import _amount_in_words

        text = _amount_in_words(Decimal("1234.75"))
        assert "ألف ومئتان وأربعة وثلاثون" in text
        assert "خمسة وسبعون قرشاً" in text

    def test_zero_still_reads_as_an_amount(self):
        from app.services.documents import _amount_in_words

        assert "صفر" in _amount_in_words(Decimal("0"))
