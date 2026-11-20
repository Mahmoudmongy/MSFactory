"""Deleting vs deactivating masters (accounts, contacts, warehouses).

A master that already holds history must never vanish, because every past
document points at it by id. These tests pin the contract: an unused master can
be deleted, a used one returns 409 and must be deactivated instead, and a
deactivated master disappears from the default lists and pickers.
"""
from __future__ import annotations

import pytest

from tests.test_api import auth, register


@pytest.fixture
def token(client):
    return register(client, email="lifecycle@acme.eg", company="مصنع دورة الحياة")


def _first_account(client, token, code: str) -> dict:
    accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
    return next(a for a in accounts if a["code"] == code)


class TestAccounts:
    def test_unused_account_can_be_deleted(self, client, token):
        created = client.post(
            "/api/v1/accounts",
            headers=auth(token),
            json={"code": "5799", "name": "مصروف تجريبي", "type": "expense"},
        )
        assert created.status_code == 201, created.text
        account_id = created.json()["id"]

        deleted = client.delete(f"/api/v1/accounts/{account_id}", headers=auth(token))
        assert deleted.status_code == 204, deleted.text

        remaining = client.get("/api/v1/accounts", headers=auth(token)).json()
        assert all(a["id"] != account_id for a in remaining)

    def test_account_with_entries_cannot_be_deleted(self, client, token):
        cash = _first_account(client, token, "1010")
        entry = client.post(
            "/api/v1/journal",
            headers=auth(token),
            json={
                "entry_date": "2026-01-05",
                "description": "قيد اختبار الحذف",
                "lines": [
                    {"account_id": cash["id"], "debit": "100", "credit": "0"},
                    {"account_id": _first_account(client, token, "3100")["id"], "debit": "0", "credit": "100"},
                ],
            },
        )
        assert entry.status_code == 201, entry.text

        response = client.delete(f"/api/v1/accounts/{cash['id']}", headers=auth(token))
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "account_in_use"
        assert detail["entries"] >= 1

    def test_account_can_be_deactivated(self, client, token):
        created = client.post(
            "/api/v1/accounts",
            headers=auth(token),
            json={"code": "5798", "name": "مصروف مؤقت", "type": "expense"},
        )
        account_id = created.json()["id"]
        updated = client.patch(
            f"/api/v1/accounts/{account_id}",
            headers=auth(token),
            json={"is_active": False},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["is_active"] is False


class TestContacts:
    def _contact(self, client, token, name: str) -> dict:
        response = client.post(
            "/api/v1/contacts",
            headers=auth(token),
            json={"name": name, "kind": "customer"},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_unused_contact_can_be_deleted(self, client, token):
        contact = self._contact(client, token, "عميل بلا حركات")
        deleted = client.delete(f"/api/v1/contacts/{contact['id']}", headers=auth(token))
        assert deleted.status_code == 204, deleted.text

    def test_contact_with_invoice_cannot_be_deleted(self, client, token):
        contact = self._contact(client, token, "عميل له فاتورة")
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-02-01",
                # A free-text line keeps the test about the contact, not stock.
                "lines": [{"description": "خدمة", "quantity": "1", "unit_price": "50"}],
            },
        )
        assert invoice.status_code == 201, invoice.text

        response = client.delete(f"/api/v1/contacts/{contact['id']}", headers=auth(token))
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "contact_in_use"

    def test_deactivated_contact_leaves_the_default_list(self, client, token):
        contact = self._contact(client, token, "عميل سيُوقف")
        client.patch(
            f"/api/v1/contacts/{contact['id']}",
            headers=auth(token),
            json={"is_active": False},
        )
        active = client.get("/api/v1/contacts", headers=auth(token)).json()
        assert all(c["id"] != contact["id"] for c in active)

        everyone = client.get(
            "/api/v1/contacts", headers=auth(token), params={"include_inactive": True}
        ).json()
        assert any(c["id"] == contact["id"] for c in everyone)

    def test_invoice_for_a_deactivated_contact_is_refused(self, client, token):
        contact = self._contact(client, token, "عميل موقوف")
        client.patch(
            f"/api/v1/contacts/{contact['id']}",
            headers=auth(token),
            json={"is_active": False},
        )
        invoice = client.post(
            "/api/v1/invoices",
            headers=auth(token),
            json={
                "kind": "sales",
                "contact_id": contact["id"],
                "issue_date": "2026-02-02",
                "lines": [{"description": "خدمة", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert invoice.status_code == 422, invoice.text


class TestWarehouses:
    def test_empty_warehouse_can_be_deleted(self, client, token):
        created = client.post(
            "/api/v1/inventory/warehouses",
            headers=auth(token),
            json={"name": "مخزن مؤقت"},
        )
        assert created.status_code == 201, created.text
        warehouse_id = created.json()["id"]

        deleted = client.delete(
            f"/api/v1/inventory/warehouses/{warehouse_id}", headers=auth(token)
        )
        assert deleted.status_code == 204, deleted.text

    def test_warehouse_with_movements_cannot_be_deleted(self, client, token):
        warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
        raw = next(w for w in warehouses if w["code"] == "RAW")
        units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
        piece = next(u for u in units if u["code"] == "PCS")
        item = client.post(
            "/api/v1/inventory/items",
            headers=auth(token),
            json={"name": "صنف حركة", "base_unit_id": piece["id"]},
        ).json()
        receive = client.post(
            "/api/v1/inventory/movements/receive",
            headers=auth(token),
            json={
                "item_id": item["id"],
                "warehouse_id": raw["id"],
                "quantity": "5",
                "unit_cost": "10",
                "movement_date": "2026-03-01",
            },
        )
        assert receive.status_code == 201, receive.text

        response = client.delete(
            f"/api/v1/inventory/warehouses/{raw['id']}", headers=auth(token)
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "warehouse_in_use"

    def test_parent_warehouse_with_children_cannot_be_deleted(self, client, token):
        warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
        main = next(w for w in warehouses if w["code"] == "MAIN")
        response = client.delete(
            f"/api/v1/inventory/warehouses/{main['id']}", headers=auth(token)
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "warehouse_has_children"

    def test_warehouse_can_be_deactivated(self, client, token):
        warehouses = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
        target = next(w for w in warehouses if w["code"] == "DMG")
        updated = client.patch(
            f"/api/v1/inventory/warehouses/{target['id']}",
            headers=auth(token),
            json={"is_active": False},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["is_active"] is False

        listed = client.get("/api/v1/inventory/warehouses", headers=auth(token)).json()
        assert all(w["id"] != target["id"] for w in listed)
