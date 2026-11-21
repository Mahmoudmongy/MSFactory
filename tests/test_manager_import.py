"""Importing a Manager.io business.

The tests run against a real HTTP server that speaks Manager's ``/api4``
protocol, not against a stub object. That matters: the importer's job is to be
right about a wire format, and a mock would only prove the importer agrees with
the mock. The fake server answers exactly as the published OpenAPI specs
describe — a ``{"items": [{"key", "item"}]}`` envelope, ``Key`` and ``Business``
as query parameters — so a wrong path or a wrong envelope shape fails here the
same way it would against a real Manager.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

API_KEY = "test-api-key-123456"
BUSINESS = "11111111-1111-1111-1111-111111111111"

CUSTOMER_KEY = "aaaa1111-0000-0000-0000-000000000001"
SUPPLIER_KEY = "aaaa1111-0000-0000-0000-000000000002"
TAX_KEY = "bbbb2222-0000-0000-0000-000000000001"
BANK_KEY = "cccc3333-0000-0000-0000-000000000001"
EXPENSE_ACCOUNT_KEY = "cccc3333-0000-0000-0000-000000000002"
ITEM_KEY = "dddd4444-0000-0000-0000-000000000001"
INVOICE_KEY = "eeee5555-0000-0000-0000-000000000001"
PURCHASE_KEY = "eeee5555-0000-0000-0000-000000000002"
RECEIPT_KEY = "ffff6666-0000-0000-0000-000000000001"


def _envelope(rows):
    return {"items": [{"key": key, "item": item} for key, item in rows], "_links": {}, "_actions": {}}


# The fixtures below mirror the field names in Manager's own OpenAPI schemas:
# ``salesUnitPrice``, ``discountPercentage``, ``trackQuantity``, ``isCashAtBank``.
DATASET = {
    "/api4/business-batch": _envelope([(BUSINESS, {"name": "مصنع النور"})]),
    "/api4/customer-batch": _envelope(
        [
            (
                CUSTOMER_KEY,
                {
                    "name": "شركة الأمل",
                    "email": "amal@example.com",
                    "obsolete_Telephone": "01000000000",
                    "billingAddress": "القاهرة",
                    "inactive": False,
                },
            )
        ]
    ),
    "/api4/supplier-batch": _envelope([(SUPPLIER_KEY, {"name": "مورد الخامات", "email": "supplier@example.com"})]),
    "/api4/tax-code-batch": _envelope([(TAX_KEY, {"name": "VAT 14%", "rate": 14})]),
    "/api4/balance-sheet-account-batch": _envelope([(BANK_KEY, {"name": "بنك مصر", "isCashAtBank": True})]),
    "/api4/profit-and-loss-statement-account-batch": _envelope(
        [(EXPENSE_ACCOUNT_KEY, {"name": "إيجار", "type": "Expense"})]
    ),
    "/api4/bank-or-cash-account-batch": _envelope([(BANK_KEY, {"name": "بنك مصر", "isCashAtBank": True})]),
    "/api4/inventory-item-batch": _envelope([(ITEM_KEY, {"name": "مسمار", "trackQuantity": True, "salePrice": 10})]),
    "/api4/non-inventory-item-batch": _envelope([]),
    "/api4/sales-invoice-batch": _envelope(
        [
            (
                INVOICE_KEY,
                {
                    "reference": "INV-1001",
                    "issueDate": "2026-01-15T00:00:00Z",
                    "customer": CUSTOMER_KEY,
                    "description": "فاتورة يناير",
                    "lines": [
                        {
                            "item": ITEM_KEY,
                            "qty": 2,
                            "salesUnitPrice": 100,
                            "discountPercentage": 0,
                            "taxCode": TAX_KEY,
                            "lineDescription": "مسمار",
                        }
                    ],
                },
            )
        ]
    ),
    "/api4/purchase-invoice-batch": _envelope(
        [
            (
                PURCHASE_KEY,
                {
                    "reference": "BILL-2001",
                    "issueDate": "2026-01-10T00:00:00Z",
                    "supplier": SUPPLIER_KEY,
                    "lines": [
                        {
                            "account": EXPENSE_ACCOUNT_KEY,
                            "qty": 1,
                            "purchaseUnitPrice": 500,
                            "lineDescription": "إيجار",
                        }
                    ],
                },
            )
        ]
    ),
    "/api4/receipt-batch": _envelope(
        [
            (
                RECEIPT_KEY,
                {
                    "reference": "RCV-1",
                    "date": "2026-01-20T00:00:00Z",
                    "customer": CUSTOMER_KEY,
                    "amount": 228,
                    "bankAccount": BANK_KEY,
                },
            )
        ]
    ),
    "/api4/payment-batch": _envelope([]),
}

SEEN_REQUESTS: list[tuple[str, dict]] = []


class _ManagerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        SEEN_REQUESTS.append((parsed.path, query))

        if query.get("Key") != API_KEY:
            return self._send(401, {"error": "unauthorized", "message": "bad key"})

        # ``Business`` is optional for the business list, required elsewhere.
        if parsed.path != "/api4/business-batch" and query.get("Business") != BUSINESS:
            return self._send(403, {"error": "forbidden", "message": "bad business"})

        payload = DATASET.get(parsed.path)
        if payload is None:
            return self._send(404, {"error": "not_found", "message": "no such endpoint"})
        return self._send(200, payload)

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # keep pytest output clean
        return


@pytest.fixture(scope="module")
def manager_server():
    server = HTTPServer(("127.0.0.1", 0), _ManagerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _clear_seen():
    SEEN_REQUESTS.clear()
    yield
    SEEN_REQUESTS.clear()


def register(client, email="importer@acme.eg", company="شركة الاستيراد"):
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


def credentials(base_url, **overrides):
    payload = {"api_key": API_KEY, "business": BUSINESS, "base_url": base_url}
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ business


def test_the_business_list_uses_the_key_and_needs_no_business_id(client, manager_server):
    """Picking a business is the first step, so it must work before one is chosen."""
    token = register(client)
    response = client.post(
        "/api/v1/imports/manager/businesses",
        headers=auth(token),
        json=credentials(manager_server, business=""),
    )
    assert response.status_code == 200, response.text
    assert response.json() == [{"key": BUSINESS, "name": "مصنع النور"}]


def test_a_wrong_api_key_is_reported_in_arabic(client, manager_server):
    token = register(client)
    response = client.post(
        "/api/v1/imports/manager/businesses",
        headers=auth(token),
        json=credentials(manager_server, api_key="wrong-key-000000"),
    )
    assert response.status_code == 422
    assert "مفتاح" in response.json()["detail"]["message"]


# ------------------------------------------------------------------- preview


def test_preview_creates_nothing_and_reports_what_it_would_do(client, manager_server):
    """A dry run must leave the books untouched — the whole point of a preview."""
    token = register(client, email="preview@acme.eg")
    before = client.get("/api/v1/accounts", headers=auth(token)).json()
    contacts_before = client.get("/api/v1/contacts", headers=auth(token)).json()

    response = client.post(
        "/api/v1/imports/manager/preview",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["counts"]["contact"]["created"] == 2
    assert body["counts"]["invoice"]["created"] == 2

    after = client.get("/api/v1/accounts", headers=auth(token)).json()
    assert len(after) == len(before)
    assert len(client.get("/api/v1/contacts", headers=auth(token)).json()) == len(contacts_before)


# ----------------------------------------------------------------------- run


def test_a_full_import_lands_contacts_accounts_items_and_documents(client, manager_server):
    token = register(client, email="full@acme.eg")
    response = client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["issues"] == []
    assert body["counts"]["contact"]["created"] == 2
    assert body["counts"]["invoice"]["created"] == 2
    assert body["counts"]["payment"]["created"] == 1

    contacts = client.get("/api/v1/contacts", headers=auth(token)).json()
    names = {row["name"] for row in contacts}
    assert {"شركة الأمل", "مورد الخامات"} <= names
    amal = next(row for row in contacts if row["name"] == "شركة الأمل")
    assert amal["email"] == "amal@example.com"
    assert amal["phone"] == "01000000000"

    invoices = client.get("/api/v1/invoices", headers=auth(token)).json()
    sales = [row for row in invoices if row["kind"] == "sales"]
    purchase = [row for row in invoices if row["kind"] == "purchase"]
    assert len(sales) == 1 and len(purchase) == 1

    # 2 x 100 with 14% VAT, computed by our own line arithmetic.
    assert sales[0]["subtotal"] == "200.0000"
    assert sales[0]["tax_total"] == "28.0000"
    assert sales[0]["total"] == "228.0000"


def test_imported_invoices_are_posted_to_the_ledger(client, manager_server):
    """An import that only filled tables would leave the trial balance empty."""
    token = register(client, email="ledger@acme.eg")
    client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    trial = client.get("/api/v1/reports/trial-balance", headers=auth(token)).json()
    debit = sum(float(row["debit"]) for row in trial["rows"])
    credit = sum(float(row["credit"]) for row in trial["rows"])
    assert debit > 0
    assert debit == pytest.approx(credit, abs=0.01)


def test_running_the_same_import_twice_does_not_duplicate_anything(client, manager_server):
    """The mapping table is what makes a retry safe; without it this doubles."""
    token = register(client, email="twice@acme.eg")
    body = {"credentials": credentials(manager_server)}
    client.post("/api/v1/imports/manager/run", headers=auth(token), json=body)
    contacts_after_first = len(client.get("/api/v1/contacts", headers=auth(token)).json())

    second = client.post("/api/v1/imports/manager/run", headers=auth(token), json=body)
    assert second.status_code == 200, second.text
    counts = second.json()["counts"]
    # Nothing is created twice; the existing rows are refreshed instead.
    assert counts["contact"]["created"] == 0
    assert counts["contact"]["updated"] == 2
    assert counts["invoice"]["created"] == 0
    assert len(client.get("/api/v1/contacts", headers=auth(token)).json()) == contacts_after_first


def test_a_bank_account_becomes_a_child_of_1020_not_a_new_root(client, manager_server):
    token = register(client, email="bank@acme.eg")
    client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
    bank_root = next(row for row in accounts if row["code"] == "1020")
    imported = next(row for row in accounts if row["name"] == "بنك مصر")
    assert imported["parent_id"] == bank_root["id"]
    assert imported["code"].startswith("102")
    assert imported["type"] == "asset"


def test_an_expense_account_lands_under_the_expense_heading(client, manager_server):
    token = register(client, email="expense@acme.eg")
    client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    accounts = client.get("/api/v1/accounts", headers=auth(token)).json()
    imported = next(row for row in accounts if row["name"] == "إيجار")
    assert imported["type"] == "expense"
    assert imported["code"].startswith("59")


def test_the_since_option_skips_older_documents(client, manager_server):
    token = register(client, email="since@acme.eg")
    response = client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server), "options": {"since": "2026-01-14"}},
    )
    assert response.status_code == 200, response.text
    counts = response.json()["counts"]
    # The purchase invoice is dated the 10th, the sales invoice the 15th.
    assert counts["invoice"]["created"] == 1
    assert counts["invoice"]["skipped"] == 1


def test_the_api_key_is_not_persisted_anywhere(client, manager_server, db):
    """A database leak must not leak a customer's Manager credentials."""
    from sqlalchemy import text

    token = register(client, email="secret@acme.eg")
    client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    rows = db.execute(text("SELECT report::text, business_key, business_name FROM import_jobs")).all()
    assert rows
    for report, business_key, business_name in rows:
        assert API_KEY not in (report or "")
        assert API_KEY not in (business_key or "")
        assert API_KEY not in (business_name or "")


def test_a_second_tenant_cannot_see_the_first_ones_import(client, manager_server):
    first = register(client, email="tenant-a@acme.eg", company="شركة أ")
    client.post(
        "/api/v1/imports/manager/run",
        headers=auth(first),
        json={"credentials": credentials(manager_server)},
    )
    second = register(client, email="tenant-b@acme.eg", company="شركة ب")
    assert client.get("/api/v1/contacts", headers=auth(second)).json() == []
    assert client.get("/api/v1/imports/manager/jobs", headers=auth(second)).json() == []


def test_the_job_log_records_the_run(client, manager_server):
    token = register(client, email="jobs@acme.eg")
    client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": credentials(manager_server)},
    )
    jobs = client.get("/api/v1/imports/manager/jobs", headers=auth(token)).json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["dry_run"] is False
    assert jobs[0]["report"]["counts"]["contact"]["created"] == 2


def test_a_failed_import_is_recorded_but_changes_nothing(client, manager_server):
    """A failure must roll the books back and still leave a trail to debug.

    Without the trail, the user's only evidence is "it didn't work"; without the
    rollback, a half-imported chart of accounts is worse than none.
    """
    token = register(client, email="failure@acme.eg")
    before = len(client.get("/api/v1/accounts", headers=auth(token)).json())

    broken = dict(credentials(manager_server), api_key="wrong-key")
    response = client.post(
        "/api/v1/imports/manager/run",
        headers=auth(token),
        json={"credentials": broken},
    )
    assert response.status_code >= 400

    after = len(client.get("/api/v1/accounts", headers=auth(token)).json())
    assert after == before, "a failed import must not leave accounts behind"

    jobs = client.get("/api/v1/imports/manager/jobs", headers=auth(token)).json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["error"]


# ------------------------------------------------------------------ the screen


class TestImportScreen:
    """The import screen is built at runtime, so a broken link is silent.

    These check the shipped JavaScript rather than a rendered DOM: the screen
    still opens when it is wired wrong, it just stops offering the preview or
    stops reaching the route, and nobody notices until a migration goes wrong.
    """

    @pytest.fixture(scope="class")
    def app_js(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")

    def test_the_route_exists_and_is_reachable_from_settings(self, app_js):
        assert 'case "import":' in app_js
        assert "return renderImport();" in app_js
        assert 'href="#/import"' in app_js, "settings must link to the import screen"

    def test_the_screen_calls_every_endpoint_it_needs(self, app_js):
        for path in ("/imports/manager/businesses", "/imports/manager/preview", "/imports/manager/run"):
            assert path in app_js, f"{path} is unreachable from the UI"
        assert "/imports/manager/jobs" in app_js

    def test_a_preview_is_offered_before_the_real_run(self, app_js):
        """Preview-first is the safety property; it must not be dropped."""
        body = app_js[app_js.index("async function renderImport()") :]
        body = body[: body.index("function importReport(")]
        assert '"preview"' in body
        assert 'id="preview"' in body
        assert 'id="run"' in body
        assert "confirmDialog" in body, "the real run must ask before it writes"

    def test_the_key_field_is_masked_and_not_stored(self, app_js):
        body = app_js[app_js.index("async function renderImport()") :]
        body = body[: body.index("function importReport(")]
        assert 'type="password"' in body
        # The key travels in the request body only; nothing writes it to storage.
        assert "localStorage" not in body
        assert "sessionStorage" not in body
