"""End-to-end API tests covering the accounting invariants that matter most."""
from __future__ import annotations

from decimal import Decimal

import pytest


def register(client, email="owner@acme.eg", company="شركة أكمي"):
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


@pytest.fixture
def token(client):
    return register(client)


class TestAuth:
    def test_register_provisions_a_full_chart_of_accounts(self, client, token):
        accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
        codes = {a["code"] for a in accounts}
        assert {"1010", "1100", "2100", "2200", "4100", "5100"} <= codes

    def test_login_returns_tokens(self, client):
        register(client, email="login@acme.eg")
        response = client.post(
            "/api/v1/auth/login", json={"email": "login@acme.eg", "password": "Str0ng!Pass"}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_wrong_password_is_rejected(self, client):
        register(client, email="wrong@acme.eg")
        response = client.post(
            "/api/v1/auth/login", json={"email": "wrong@acme.eg", "password": "nope-not-it"}
        )
        assert response.status_code == 401

    def test_unauthenticated_request_is_rejected(self, client):
        assert client.get("/api/v1/accounts").status_code in (401, 403)


class TestTenantIsolation:
    def test_companies_cannot_see_each_others_data(self, client):
        token_a = register(client, email="a@one.eg", company="Company A")
        token_b = register(client, email="b@two.eg", company="Company B")

        created = client.post(
            "/api/v1/contacts", headers=auth(token_a), json={"name": "عميل أ", "kind": "customer"}
        ).json()

        assert client.get("/api/v1/contacts", headers=auth(token_b)).json() == []
        # Company B must not be able to read Company A's contact by id.
        assert client.get(f"/api/v1/contacts/{created['id']}/statement", headers=auth(token_b)).status_code == 404

    def test_cannot_switch_to_a_company_without_membership(self, client):
        token_a = register(client, email="c@one.eg", company="Company C")
        register(client, email="d@two.eg", company="Company D")
        response = client.get("/api/v1/accounts", headers={**auth(token_a), "X-Company-Id": "2"})
        assert response.status_code == 403


class TestInvoiceLifecycle:
    def test_issuing_an_invoice_balances_the_books(self, client, token):
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل", "kind": "customer"}
        ).json()
        response = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-01-15",
                "lines": [{"description": "بند", "quantity": 2, "unit_price": 1000, "tax_rate": 14}],
            },
        )
        assert response.status_code == 201, response.text
        invoice = response.json()
        assert Decimal(invoice["total"]) == Decimal("2280.00")
        assert Decimal(invoice["tax_total"]) == Decimal("280.00")

        trial = client.get("/api/v1/reports/trial-balance", headers=auth(token)).json()
        assert trial["total_debit"] == trial["total_credit"]
        sheet = client.get("/api/v1/reports/balance-sheet", headers=auth(token)).json()
        assert sheet["balanced"] is True

    def test_payment_allocates_to_the_invoice(self, client, token):
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل دفع", "kind": "customer"}
        ).json()
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-02-01",
                "lines": [{"description": "بند", "quantity": 1, "unit_price": 1000}],
            },
        ).json()
        cash = next(a for a in client.get("/api/v1/accounts", headers=auth(token)).json() if a["code"] == "1010")

        response = client.post(
            "/api/v1/payments",
            headers=auth(token),
            json={
                "kind": "receive",
                "contact_id": contact["id"],
                "date": "2026-02-10",
                "amount": 600,
                "bank_account_id": cash["id"],
            },
        )
        assert response.status_code == 201, response.text
        payment = response.json()
        assert Decimal(payment["allocations"][0]["amount"]) == Decimal("600.00")

        refreshed = client.get(f"/api/v1/invoices/{invoice['id']}", headers=auth(token)).json()
        assert refreshed["status"] == "partially_paid"
        assert Decimal(refreshed["amount_paid"]) == Decimal("600.00")

    def test_payment_can_target_a_specific_invoice(self, client, token):
        """Naming an invoice must beat FIFO, so paying a newer bill works."""
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل فواتير متعددة", "kind": "customer"}
        ).json()
        cash = next(a for a in client.get("/api/v1/accounts", headers=auth(token)).json() if a["code"] == "1010")

        def make_invoice(issue_date, amount):
            return client.post(
                "/api/v1/invoices",
                headers=auth(token),
                json={
                    "kind": "sales",
                    "contact_id": contact["id"],
                    "issue_date": issue_date,
                    "lines": [{"description": "بند", "quantity": 1, "unit_price": amount}],
                },
            ).json()

        older = make_invoice("2026-03-01", 500)
        newer = make_invoice("2026-03-20", 700)

        response = client.post(
            "/api/v1/payments",
            headers=auth(token),
            json={
                "kind": "receive",
                "contact_id": contact["id"],
                "date": "2026-03-25",
                "amount": 700,
                "bank_account_id": cash["id"],
                "invoice_id": newer["id"],
            },
        )
        assert response.status_code == 201, response.text
        assert [a["invoice_id"] for a in response.json()["allocations"]] == [newer["id"]]

        assert client.get(f"/api/v1/invoices/{newer['id']}", headers=auth(token)).json()["status"] == "paid"
        assert client.get(f"/api/v1/invoices/{older['id']}", headers=auth(token)).json()["status"] == "issued"

    def test_payment_rejects_an_invoice_belonging_to_another_contact(self, client, token):
        owner = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "مالك الفاتورة", "kind": "customer"}
        ).json()
        other = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل آخر", "kind": "customer"}
        ).json()
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": owner["id"],
                "issue_date": "2026-04-01",
                "lines": [{"description": "بند", "quantity": 1, "unit_price": 300}],
            },
        ).json()
        cash = next(a for a in client.get("/api/v1/accounts", headers=auth(token)).json() if a["code"] == "1010")

        response = client.post(
            "/api/v1/payments",
            headers=auth(token),
            json={
                "kind": "receive",
                "contact_id": other["id"],
                "date": "2026-04-05",
                "amount": 300,
                "bank_account_id": cash["id"],
                "invoice_id": invoice["id"],
            },
        )
        assert response.status_code == 422, response.text

        # The refused payment must leave no trace on the invoice.
        assert client.get(f"/api/v1/invoices/{invoice['id']}", headers=auth(token)).json()["amount_paid"] == "0.0000"

    def test_voiding_an_invoice_reverses_its_entry(self, client, token):
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل إلغاء", "kind": "customer"}
        ).json()
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-03-01",
                "lines": [{"description": "بند", "quantity": 1, "unit_price": 500}],
            },
        ).json()

        response = client.post(f"/api/v1/invoices/{invoice['id']}/void", headers=auth(token), json={"reason": "خطأ"})
        assert response.status_code == 200
        assert response.json()["status"] == "void"

        # The reversal nets the books back to zero for that invoice.
        sheet = client.get("/api/v1/reports/balance-sheet", headers=auth(token)).json()
        assert sheet["balanced"] is True
        assert Decimal(sheet["assets"]) == Decimal("0.00")

    def test_voiding_a_paid_invoice_is_refused(self, client, token):
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل مدفوع", "kind": "customer"}
        ).json()
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-04-01",
                "lines": [{"description": "بند", "quantity": 1, "unit_price": 100}],
            },
        ).json()
        cash = next(a for a in client.get("/api/v1/accounts", headers=auth(token)).json() if a["code"] == "1010")
        client.post(
            "/api/v1/payments",
            headers=auth(token),
            json={
                "kind": "receive",
                "contact_id": contact["id"],
                "date": "2026-04-05",
                "amount": 100,
                "bank_account_id": cash["id"],
            },
        )
        response = client.post(f"/api/v1/invoices/{invoice['id']}/void", headers=auth(token), json={})
        assert response.status_code == 409


class TestIdempotency:
    def test_repeated_request_creates_one_invoice(self, client, token):
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل مكرر", "kind": "customer"}
        ).json()
        body = {
            "kind": "sales",
            "contact_id": contact["id"],
            "issue_date": "2026-05-01",
            "lines": [{"description": "بند", "quantity": 1, "unit_price": 100}],
        }
        headers = {**auth(token), "Idempotency-Key": "retry-1"}

        first = client.post("/api/v1/invoices", headers=headers, json=body)
        second = client.post("/api/v1/invoices", headers=headers, json=body)

        assert first.status_code == second.status_code == 201
        assert first.json()["number"] == second.json()["number"]
        assert len(client.get("/api/v1/invoices", headers=auth(token)).json()) == 1


class TestJournalIntegrity:
    def test_unbalanced_entry_is_rejected(self, client, token):
        accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
        response = client.post(
            "/api/v1/journal",
            headers=auth(token),
            json={
                "entry_date": "2026-06-01",
                "memo": "غير متوازن",
                "lines": [
                    {"account_id": accounts[0]["id"], "debit": 100},
                    {"account_id": accounts[1]["id"], "credit": 50},
                ],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unbalanced_entry"

    def test_reversal_is_idempotent_and_locked(self, client, token):
        accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
        entry = client.post(
            "/api/v1/journal",
            headers=auth(token),
            json={
                "entry_date": "2026-07-01",
                "memo": "قيد",
                "lines": [
                    {"account_id": accounts[0]["id"], "debit": 100},
                    {"account_id": accounts[1]["id"], "credit": 100},
                ],
            },
        ).json()

        first = client.post(f"/api/v1/journal/{entry['id']}/reverse", headers=auth(token), json={})
        assert first.status_code == 200
        assert first.json()["reverses_entry_id"] == entry["id"]

        second = client.post(f"/api/v1/journal/{entry['id']}/reverse", headers=auth(token), json={})
        assert second.status_code == 409

    def test_closed_period_rejects_new_entries(self, client, token):
        accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
        assert client.post("/api/v1/reports/periods/2026/8/close", headers=auth(token)).status_code == 200
        response = client.post(
            "/api/v1/journal",
            headers=auth(token),
            json={
                "entry_date": "2026-08-20",
                "memo": "مغلق",
                "lines": [
                    {"account_id": accounts[0]["id"], "debit": 10},
                    {"account_id": accounts[1]["id"], "credit": 10},
                ],
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "period_closed"


class TestReports:
    def test_reports_are_always_balanced(self, client, token):
        for report in ("trial-balance", "balance-sheet", "income-statement", "ar-aging", "dashboard"):
            assert client.get(f"/api/v1/reports/{report}", headers=auth(token)).status_code == 200

        sheet = client.get("/api/v1/reports/balance-sheet", headers=auth(token)).json()
        assert sheet["balanced"] is True
        trial = client.get("/api/v1/reports/trial-balance", headers=auth(token)).json()
        assert trial["total_debit"] == trial["total_credit"]


class TestRBAC:
    def test_viewer_cannot_create_an_invoice(self, client, token, db):
        from app.core.security import hash_password
        from app.db.models import Membership, User

        viewer = User(email="viewer@acme.eg", full_name="مشاهد", password_hash=hash_password("Str0ng!Pass"))
        db.add(viewer)
        db.flush()
        company_id = client.get("/api/v1/auth/me", headers=auth(token)).json()["memberships"][0]["company"]["id"]
        db.add(Membership(user_id=viewer.id, company_id=company_id, role="viewer"))
        db.commit()

        login = client.post(
            "/api/v1/auth/login", json={"email": "viewer@acme.eg", "password": "Str0ng!Pass"}
        ).json()
        response = client.post(
            "/api/v1/invoices",
            headers={**auth(login["access_token"]), "X-Company-Id": str(company_id)},
            json={
                "kind": "sales",
                "contact_id": 1,
                "issue_date": "2026-09-01",
                "lines": [{"description": "بند", "quantity": 1, "unit_price": 10}],
            },
        )
        assert response.status_code == 403


class TestSettings:
    def test_company_is_provisioned_with_configurable_tax_codes(self, client, token):
        """Egypt's 14% VAT must be data, so a company can change it without code."""
        response = client.get("/api/v1/settings", headers=auth(token))
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["settings"]["vat_rate"] == "14"
        assert data["settings"]["currency"] == "EGP"
        rates = {code["name"]: Decimal(code["rate"]) for code in data["tax_codes"]}
        assert rates["VAT 14%"] == Decimal("14.0000")
        assert any(code["kind"] == "withholding" for code in data["tax_codes"])

    def test_vat_rate_can_be_changed_and_drives_invoice_totals(self, client, token):
        """Change the rate in settings, and new invoices must use it."""
        assert (
            client.patch("/api/v1/settings", headers=auth(token), json={"settings": {"vat_rate": "5"}}).status_code
            == 200
        )

        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "عميل ضريبة", "kind": "customer"}
        ).json()
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-05-01",
                "lines": [{"description": "بند", "quantity": 1, "unit_price": 1000, "tax_rate": 5}],
            },
        ).json()
        assert Decimal(invoice["tax_total"]) == Decimal("50.00")
        assert Decimal(invoice["total"]) == Decimal("1050.00")

    def test_unknown_setting_is_rejected(self, client, token):
        response = client.patch(
            "/api/v1/settings", headers=auth(token), json={"settings": {"not_a_real_key": "x"}}
        )
        assert response.status_code == 422, response.text

    def test_custom_tax_code_can_be_created_updated_and_retired(self, client, token):
        created = client.post(
            "/api/v1/settings/tax-codes",
            headers=auth(token),
            json={"name_ar": "ضريبة خدمات 10%", "name": "Service 10%", "rate": 10, "kind": "vat"},
        )
        assert created.status_code == 201, created.text
        code = created.json()
        assert Decimal(code["rate"]) == Decimal("10.0000")

        updated = client.patch(
            f"/api/v1/settings/tax-codes/{code['id']}", headers=auth(token), json={"rate": 12}
        )
        assert updated.status_code == 200, updated.text
        assert Decimal(updated.json()["rate"]) == Decimal("12.0000")

        assert client.delete(f"/api/v1/settings/tax-codes/{code['id']}", headers=auth(token)).status_code == 204
        codes = client.get("/api/v1/settings", headers=auth(token)).json()["tax_codes"]
        retired = next(c for c in codes if c["id"] == code["id"])
        assert retired["is_active"] is False

    def test_negative_tax_rate_is_rejected(self, client, token):
        response = client.post(
            "/api/v1/settings/tax-codes",
            headers=auth(token),
            json={"name_ar": "سالب", "rate": -5, "kind": "vat"},
        )
        assert response.status_code == 422, response.text

    def test_a_viewer_cannot_change_settings(self, client, token, db):
        from app.db.models import Membership, User

        viewer = User(email="viewer@acme.eg", password_hash="x", full_name="مشاهد")
        db.add(viewer)
        db.flush()
        owner = db.query(User).filter(User.email == "owner@acme.eg").one()
        membership = db.query(Membership).filter(Membership.user_id == owner.id).one()
        db.add(Membership(user_id=viewer.id, company_id=membership.company_id, role="viewer"))
        db.commit()

        from app.core.security import hash_password

        viewer.password_hash = hash_password("Str0ng!Pass")
        db.commit()

        login = client.post(
            "/api/v1/auth/login",
            json={"email": "viewer@acme.eg", "password": "Str0ng!Pass", "company_id": membership.company_id},
        )
        assert login.status_code == 200, login.text
        viewer_token = login.json()["access_token"]

        response = client.patch(
            "/api/v1/settings", headers=auth(viewer_token), json={"settings": {"vat_rate": "1"}}
        )
        assert response.status_code == 403, response.text

    def test_the_company_logo_survives_a_save_and_reload(self, client, token):
        """The settings screen uploads a logo; every later read must return it."""
        # Bigger than the 500-character column the other settings used to fit in.
        logo = "data:image/png;base64," + "A" * 900

        saved = client.patch(
            "/api/v1/settings", headers=auth(token), json={"settings": {"logo": logo, "company_name": "مصنع النور"}}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"]["logo"] == logo

        reloaded = client.get("/api/v1/settings", headers=auth(token))
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["settings"]["logo"] == logo
        assert reloaded.json()["settings"]["company_name"] == "مصنع النور"

    @pytest.mark.parametrize(
        "logo",
        [
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "https://example.com/logo.png",
        ],
    )
    def test_only_real_images_can_be_stored_as_the_logo(self, client, token, logo):
        response = client.patch("/api/v1/settings", headers=auth(token), json={"settings": {"logo": logo}})
        assert response.status_code == 422, response.text

    def test_a_logo_that_is_too_large_is_refused(self, client, token):
        logo = "data:image/png;base64," + "A" * 400_001
        response = client.patch("/api/v1/settings", headers=auth(token), json={"settings": {"logo": logo}})
        assert response.status_code == 422, response.text


class TestEditEndpoints:
    """The write bridge edits rows in place; those endpoints must accept it."""

    def test_a_contact_can_be_edited(self, client, token):
        contact = client.post(
            "/api/v1/contacts",
            headers=auth(token),
            json={"name": "اسم قديم", "kind": "customer", "phone": "0100"},
        ).json()

        response = client.patch(
            f"/api/v1/contacts/{contact['id']}",
            headers=auth(token),
            json={"name": "اسم جديد", "kind": "both", "phone": "0111"},
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["name"] == "اسم جديد"
        assert updated["kind"] == "both"
        assert updated["phone"] == "0111"

    def test_editing_a_contact_keeps_untouched_fields(self, client, token):
        contact = client.post(
            "/api/v1/contacts",
            headers=auth(token),
            json={"name": "ثابت", "kind": "supplier", "email": "keep@acme.eg"},
        ).json()

        updated = client.patch(
            f"/api/v1/contacts/{contact['id']}", headers=auth(token), json={"phone": "0122"}
        ).json()
        assert updated["email"] == "keep@acme.eg"
        assert updated["kind"] == "supplier"

    def test_an_account_name_can_be_edited(self, client, token):
        accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
        leaf = next(a for a in accounts if not a["is_group"])

        response = client.patch(
            f"/api/v1/accounts/{leaf['id']}", headers=auth(token), json={"name_ar": "اسم معدّل"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["name_ar"] == "اسم معدّل"
        assert response.json()["code"] == leaf["code"]

    def test_a_contact_of_another_company_cannot_be_edited(self, client, token):
        contact = client.post(
            "/api/v1/contacts", headers=auth(token), json={"name": "خاص", "kind": "customer"}
        ).json()
        other = register(client, email="other@acme.eg", company="شركة أخرى")

        response = client.patch(
            f"/api/v1/contacts/{contact['id']}", headers=auth(other), json={"name": "مسروق"}
        )
        assert response.status_code == 404, response.text
