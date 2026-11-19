"""Cash & bank accounts screen: a sub-account is an ordinary asset account under
1010/1020, so it must appear in the balances report and roll up into its parent.

The screen adds accounts by calling POST /accounts with a parent, so these tests
exercise the same path the UI does rather than a private helper.
"""
from __future__ import annotations


def register(client, email="cash@acme.eg", company="شركة النقدية"):
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


def accounts_by_code(client, token):
    rows = client.get("/api/v1/accounts", headers=auth(token)).json()
    return {row["code"]: row for row in rows}


def test_register_provisions_cash_and_bank_roots(client):
    token = register(client)
    codes = accounts_by_code(client, token)
    assert codes["1010"]["name_ar"] == "النقدية بالصندوق"
    assert codes["1020"]["name_ar"] == "الحساب البنكي"
    assert codes["1010"]["parent_id"] == codes["1000"]["id"]
    assert codes["1020"]["parent_id"] == codes["1000"]["id"]


def test_add_a_sub_bank_account_under_1020(client):
    token = register(client, email="sub@acme.eg")
    codes = accounts_by_code(client, token)
    response = client.post(
        "/api/v1/accounts",
        headers=auth(token),
        json={
            "code": "1021",
            "name": "Bank Misr",
            "name_ar": "بنك مصر",
            "type": "asset",
            "parent_id": codes["1020"]["id"],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["parent_id"] == codes["1020"]["id"]

    after = accounts_by_code(client, token)
    assert after["1021"]["name_ar"] == "بنك مصر"
    assert after["1021"]["parent_id"] == codes["1020"]["id"]


def test_balances_report_includes_a_new_sub_account(client):
    token = register(client, email="bal@acme.eg")
    codes = accounts_by_code(client, token)
    client.post(
        "/api/v1/accounts",
        headers=auth(token),
        json={"code": "1011", "name": "Branch Till", "name_ar": "خزينة الفرع", "type": "asset", "parent_id": codes["1010"]["id"]},
    )

    report = client.get("/api/v1/reports/account-balances", headers=auth(token)).json()
    assets = {row["code"]: row for row in report["by_type"]["asset"]}
    assert "1011" in assets
    assert assets["1011"]["parent_id"] == codes["1010"]["id"]
    # A fresh account holds nothing, and the report must say 0 — not omit it.
    assert float(assets["1011"]["balance"]) == 0.0


def test_new_cash_account_reflects_a_receipt(client):
    """Money received into a brand-new till shows on that account's balance, which
    is what the cash screen reads. This is the end-to-end path a user takes."""
    token = register(client, email="flow@acme.eg")
    codes = accounts_by_code(client, token)
    till = client.post(
        "/api/v1/accounts",
        headers=auth(token),
        json={"code": "1012", "name": "New Till", "name_ar": "خزينة جديدة", "type": "asset", "parent_id": codes["1010"]["id"]},
    ).json()

    contact = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "عميل نقدي", "kind": "customer"}
    ).json()
    payment = client.post(
        "/api/v1/payments",
        headers=auth(token),
        json={
            "kind": "receive",
            "contact_id": contact["id"],
            "bank_account_id": till["id"],
            "amount": "500.00",
            "date": "2026-03-01",
        },
    )
    assert payment.status_code == 201, payment.text

    balance = client.get(f"/api/v1/accounts/{till['id']}/balance", headers=auth(token)).json()
    assert float(balance["balance"]) == 500.0


def test_voucher_entry_points_back_at_the_voucher(client):
    """The recent-activity list needs to know a voucher is already posted and to
    avoid listing its auto-generated entry a second time, so the journal has to
    carry the originating document's id, not just its type."""
    token = register(client, email="link@acme.eg")
    codes = accounts_by_code(client, token)
    contact = client.post(
        "/api/v1/contacts", headers=auth(token), json={"name": "عميل نقدي", "kind": "customer"}
    ).json()
    payment = client.post(
        "/api/v1/payments",
        headers=auth(token),
        json={
            "kind": "receive",
            "contact_id": contact["id"],
            "bank_account_id": codes["1010"]["id"],
            "amount": "700.00",
            "date": "2026-03-02",
        },
    ).json()

    entries = client.get("/api/v1/journal", headers=auth(token)).json()
    linked = [e for e in entries if e["source_type"] == "payment"]
    assert linked, "the voucher should have posted an entry"
    assert linked[0]["source_id"] == payment["id"]
