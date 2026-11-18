"""The audit trail (السجل الزمني): every posted document leaves a readable row.

The services already write an ``AuditLog`` row as a side effect of doing work.
These tests pin down the read side: the trail is per-company, it is newest
first, it carries the acting user's name rather than an id, and its filters
(search, action, date range) narrow it without leaking another company's rows.
"""
from __future__ import annotations

from tests.test_api import auth, register


def _create_customer(client, token, name="عميل"):
    return client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": name, "kind": "customer"}
    ).json()


def _issue_invoice(client, token, contact_id):
    return client.post(
        "/api/v1/invoices",
        headers=auth(token),
        json={
            "kind": "sales",
            "contact_id": contact_id,
            "issue_date": "2026-11-10",
            "lines": [{"description": "بند", "quantity": "1", "unit_price": "100"}],
        },
    ).json()


def test_posting_a_document_writes_a_readable_trail_row(client):
    token = register(client, email="audit@acme.eg", company="شركة السجل")
    customer = _create_customer(client, token)
    invoice = _issue_invoice(client, token, customer["id"])

    page = client.get("/api/v1/audit-logs", headers=auth(token)).json()
    assert page["total"] >= 1
    row = next(r for r in page["items"] if r["action"] == "invoice.create")
    assert row["entity_type"] == "invoice"
    assert row["entity_id"] == invoice["id"]
    # The name, not the id, so the screen can show a person.
    assert row["user_name"]
    assert row["summary"]


def test_trail_is_newest_first(client):
    token = register(client, email="order@acme.eg", company="شركة الترتيب")
    customer = _create_customer(client, token)
    _issue_invoice(client, token, customer["id"])
    _issue_invoice(client, token, customer["id"])

    items = client.get("/api/v1/audit-logs", headers=auth(token)).json()["items"]
    stamps = [item["created_at"] for item in items]
    assert stamps == sorted(stamps, reverse=True)


def test_search_and_action_filters_narrow_the_trail(client):
    token = register(client, email="filters@acme.eg", company="شركة الفلاتر")
    customer = _create_customer(client, token)
    invoice = _issue_invoice(client, token, customer["id"])
    client.post(f"/api/v1/invoices/{invoice['id']}/void", headers=auth(token), json={"reason": "خطأ"})

    only_voids = client.get("/api/v1/audit-logs?action=invoice.void", headers=auth(token)).json()
    assert only_voids["items"]
    assert {row["action"] for row in only_voids["items"]} == {"invoice.void"}

    # Search matches the summary text, which is Arabic, so search for a word the
    # void summary contains ("إلغاء").
    searched = client.get("/api/v1/audit-logs?search=إلغاء", headers=auth(token)).json()
    assert searched["items"]
    assert all("إلغاء" in row["summary"] for row in searched["items"])


def test_actions_endpoint_lists_only_this_company_codes(client):
    token = register(client, email="codes@acme.eg", company="شركة الأكواد")
    customer = _create_customer(client, token)
    _issue_invoice(client, token, customer["id"])

    other = register(client, email="other@acme.eg", company="شركة أخرى")
    _create_customer(client, other, name="عميل آخر")

    codes = client.get("/api/v1/audit-logs/actions", headers=auth(token)).json()
    assert "invoice.create" in codes
    # The other company never posted an invoice, so its actions must not appear.
    other_codes = client.get("/api/v1/audit-logs/actions", headers=auth(other)).json()
    assert "invoice.create" not in other_codes


def test_trail_never_leaks_another_company(client):
    mine = register(client, email="mine@acme.eg", company="شركتي")
    theirs = register(client, email="theirs@acme.eg", company="شركتهم")
    _create_customer(client, theirs, name="عميل شركتهم")
    my_customer = _create_customer(client, mine, name="عميلي")
    _issue_invoice(client, mine, my_customer["id"])

    page = client.get("/api/v1/audit-logs?limit=500", headers=auth(mine)).json()
    assert page["items"]
    # Nothing in my trail mentions the other company's contact.
    assert all("شركتهم" not in row["summary"] for row in page["items"])
