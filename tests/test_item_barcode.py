"""Item barcode: typed on the item form, stored on its variant, read back.

The barcode is not a column on ``items`` — it belongs to a variant, because a
colour or a size scans differently. Most items have exactly one variant, so the
API exposes the code as if it were the item's own and these tests pin that
translation in both directions.
"""
from __future__ import annotations


def register(client, email="barcode@acme.eg", company="شركة الباركود"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Str0ng!Pass", "full_name": "مالك", "company_name": company},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def create_item(client, token, **overrides):
    body = {"name": "ورق A4", "name_ar": "ورق A4", **overrides}
    response = client.post("/api/v1/inventory/items", json=body, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_barcode_is_saved_when_the_item_is_created(client):
    token = register(client)
    item = create_item(client, token, barcode="6221031401234")
    assert item["barcode"] == "6221031401234"

    # It must survive a fresh read, not just echo back from the request body.
    fetched = client.get(f"/api/v1/inventory/items/{item['id']}", headers=auth(token)).json()
    assert fetched["barcode"] == "6221031401234"
    # And it is really on the variant, which is what a scan resolves against.
    assert fetched["variants"][0]["barcode"] == "6221031401234"


def test_barcode_is_optional_and_defaults_to_empty(client):
    token = register(client, email="barcode-empty@acme.eg")
    item = create_item(client, token)
    assert item["barcode"] == ""


def test_barcode_can_be_changed_and_cleared(client):
    token = register(client, email="barcode-edit@acme.eg")
    item = create_item(client, token, barcode="111")
    path = f"/api/v1/inventory/items/{item['id']}"

    updated = client.patch(path, json={"barcode": "222"}, headers=auth(token))
    assert updated.status_code == 200, updated.text
    assert updated.json()["barcode"] == "222"
    assert client.get(path, headers=auth(token)).json()["barcode"] == "222"

    cleared = client.patch(path, json={"barcode": ""}, headers=auth(token))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["barcode"] == ""


def test_editing_other_fields_leaves_the_barcode_alone(client):
    """A PATCH without a barcode key must not wipe the stored one."""
    token = register(client, email="barcode-keep@acme.eg")
    item = create_item(client, token, barcode="999")
    path = f"/api/v1/inventory/items/{item['id']}"

    response = client.patch(path, json={"sale_price": 42}, headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["barcode"] == "999"


def test_barcode_appears_in_the_item_list(client):
    token = register(client, email="barcode-list@acme.eg")
    create_item(client, token, barcode="555")
    rows = client.get("/api/v1/inventory/items", headers=auth(token)).json()
    assert [row["barcode"] for row in rows] == ["555"]


def test_item_with_variants_keeps_each_variant_barcode(client):
    """Colours scan differently, so each variant carries its own code."""
    token = register(client, email="barcode-variants@acme.eg")
    item = create_item(
        client,
        token,
        variants=[
            {"colour": "أحمر", "barcode": "1001"},
            {"colour": "أزرق", "barcode": "1002"},
        ],
    )
    barcodes = [variant["barcode"] for variant in item["variants"]]
    assert barcodes == ["1001", "1002"]
    # The item-level barcode mirrors the first variant, which is what a
    # single-field form would have written.
    assert item["barcode"] == "1001"


def test_scanning_a_barcode_resolves_to_its_item(client):
    """What the invoice scanner does: one code in, one item out."""
    token = register(client, email="barcode-scan@acme.eg")
    item = create_item(client, token, barcode="6221031407777")

    found = client.get("/api/v1/inventory/items/by-barcode/6221031407777", headers=auth(token))
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["id"] == item["id"]
    assert body["barcode"] == "6221031407777"
    # The scanner needs the variants to know what it is adding.
    assert body["variants"][0]["barcode"] == "6221031407777"


def test_scanning_an_unknown_barcode_is_a_404_not_an_empty_row(client):
    token = register(client, email="barcode-miss@acme.eg")
    create_item(client, token, barcode="6221031408888")

    response = client.get("/api/v1/inventory/items/by-barcode/0000000000000", headers=auth(token))
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "barcode_not_found"


def test_a_barcode_prefix_does_not_match_a_longer_code(client):
    """Exact match only: '622' must not resolve to '6221031409999'."""
    token = register(client, email="barcode-exact@acme.eg")
    create_item(client, token, barcode="6221031409999")

    response = client.get("/api/v1/inventory/items/by-barcode/622", headers=auth(token))
    assert response.status_code == 404, response.text


def test_searching_the_item_list_finds_a_barcode(client):
    token = register(client, email="barcode-search@acme.eg")
    create_item(client, token, name="صنف أول", name_ar="صنف أول", barcode="900100200")
    create_item(client, token, name="صنف ثانٍ", name_ar="صنف ثانٍ", barcode="900100300")

    rows = client.get("/api/v1/inventory/items?search=9001002", headers=auth(token)).json()
    assert [row["name_ar"] for row in rows] == ["صنف أول"]


def test_barcode_lookup_stays_inside_the_company(client):
    """A code in another tenant's books must not resolve here."""
    token_a = register(client, email="barcode-tenant-a@acme.eg", company="شركة أ")
    create_item(client, token_a, barcode="7111222333")

    token_b = register(client, email="barcode-tenant-b@acme.eg", company="شركة ب")
    response = client.get("/api/v1/inventory/items/by-barcode/7111222333", headers=auth(token_b))
    assert response.status_code == 404, response.text
