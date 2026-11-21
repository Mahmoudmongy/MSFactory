"""Code 128 barcodes and the label sheet that prints them.

A barcode that encodes the wrong value is worse than no barcode: it scans
cleanly and puts the wrong item on the invoice. So the encoder is checked
against known-good module strings from ISO/IEC 15417 rather than against
itself, and the sheet is checked for the two failure modes that matter —
a skipped item and an unescaped name.
"""
import pytest

from app.services.barcode import MODULE_MM, encode, fits, is_encodable, to_svg, width_mm
from tests.test_api import auth, register

# The three widths that make up a single-symbol barcode, spelled out so the
# expected strings below can be read against ISO/IEC 15417 by eye.
_START_B = "11010010000"   # symbol 104, widths 211214
_STOP = "1100011101011"    # symbol 106, widths 2331112


class TestEncoding:
    def test_a_single_character_matches_the_standard(self):
        """Start B, "A" (symbol 33), the check symbol, then stop.

        "A" is a complete barcode on its own, so the whole module string is
        short enough to write down and check against the specification:
        104 + 1*33 = 137, and 137 % 103 = 34 is the check symbol.
        """
        assert encode("A") == (
            _START_B
            + "10100011000"   # 33: widths 111323
            + "10001011000"   # 34: widths 131123
            + _STOP
        )

    def test_every_barcode_starts_and_stops_correctly(self):
        for value in ["A", "SKU-1", "1234567890", "a b/c_d.e"]:
            modules = encode(value)
            assert modules.startswith(_START_B)
            assert modules.endswith(_STOP)

    def test_the_check_symbol_is_the_weighted_sum_modulo_103(self):
        # The check symbol for "A" is 34, whose widths are 131123.
        assert encode("A")[-len(_STOP) - 11 : -len(_STOP)] == "10001011000"

    def test_a_longer_value_is_longer(self):
        assert len(encode("AB")) > len(encode("A"))

    def test_the_printed_width_follows_the_module_size(self):
        assert width_mm("A") == pytest.approx(len(encode("A")) * MODULE_MM)

    def test_a_short_code_fits_a_label(self):
        assert fits("SHIRT-001", 46.0)

    def test_a_code_too_long_for_the_label_does_not_fit(self):
        """It would be squeezed below a scannable module width, so say so."""
        assert not fits("A" * 24, 46.0)

    def test_every_symbol_has_a_pattern(self):
        for char in "ABCabc0123-_. /":
            assert is_encodable(char)

    def test_arabic_is_rejected(self):
        assert not is_encodable("صنف")

    def test_an_unencodable_character_raises_rather_than_being_dropped(self):
        with pytest.raises(ValueError):
            encode("صنف")

    def test_an_empty_value_raises(self):
        with pytest.raises(ValueError):
            encode("")


class TestSvg:
    def test_the_svg_carries_the_value_as_its_label(self):
        svg = to_svg("SKU-1")
        assert 'aria-label="SKU-1"' in svg
        assert "<svg" in svg and "</svg>" in svg

    def test_bars_are_not_anti_aliased(self):
        """Grey edges are what make a printed barcode fail to scan."""
        assert 'shape-rendering="crispEdges"' in to_svg("X1")

    def test_the_svg_is_sized_in_millimetres_not_stretched(self):
        """``width:100%`` would compress a long code until the bars merge."""
        svg = to_svg("SHIRT-001")
        assert "width:33.5mm" in svg
        # ``max-width:100%`` may stay as an overflow guard; a bare ``width:100%``
        # is what would stretch the bars.
        assert ";width:100%" not in svg

    def test_a_name_cannot_inject_markup(self):
        svg = to_svg('"><script>alert(1)</script>')
        assert "<script>" not in svg


@pytest.fixture
def labels_setup(client):
    token = register(client, email="labels@acme.eg", company="متجر الملصقات")
    units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
    piece = next(u for u in units if u["code"] == "PCS")
    made = []
    for name, barcode in [("قميص", "SHIRT-001"), ("بنطلون", "PANT-002"), ("بدون", "")]:
        response = client.post(
            "/api/v1/inventory/items",
            headers=auth(token),
            json={
                "name": name,
                "name_ar": name,
                "item_type": "purchased",
                "base_unit_id": piece["id"],
                "barcode": barcode,
            },
        )
        assert response.status_code == 201, response.text
        made.append(response.json())
    return {"token": token, "items": made}


def label_path(client, token):
    response = client.post("/api/v1/inventory/item-labels/print-token", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["path"]


class TestLabelSheet:
    def test_the_sheet_prints_only_items_with_a_barcode(self, client, labels_setup):
        response = client.get(f"/api/v1{label_path(client, labels_setup['token'])}")
        assert response.status_code == 200
        assert "SHIRT-001" in response.text
        assert "PANT-002" in response.text
        assert "بدون" not in response.text

    def test_the_sheet_can_be_limited_to_chosen_items(self, client, labels_setup):
        shirt = labels_setup["items"][0]
        response = client.get(
            f"/api/v1{label_path(client, labels_setup['token'])}&ids={shirt['id']}"
        )
        assert "SHIRT-001" in response.text
        assert "PANT-002" not in response.text

    def test_the_sheet_is_not_cacheable(self, client, labels_setup):
        response = client.get(f"/api/v1{label_path(client, labels_setup['token'])}")
        assert response.headers["cache-control"] == "no-store"

    def test_a_bad_token_is_rejected(self, client, labels_setup):
        response = client.get("/api/v1/documents/item-labels?t=nope")
        assert response.status_code == 401

    def test_another_companys_items_are_not_included(self, client, labels_setup):
        other_token = register(client, email="other@shop.eg", company="متجر آخر")
        units = client.get("/api/v1/inventory/units", headers=auth(other_token)).json()
        piece = next(u for u in units if u["code"] == "PCS")
        stranger = client.post(
            "/api/v1/inventory/items",
            headers=auth(other_token),
            json={
                "name": "صنف غريب",
                "name_ar": "صنف غريب",
                "item_type": "purchased",
                "base_unit_id": piece["id"],
                "barcode": "FOREIGN-9",
            },
        ).json()
        response = client.get(
            f"/api/v1{label_path(client, labels_setup['token'])}&ids={stranger['id']}"
        )
        assert "FOREIGN-9" not in response.text

    def test_an_over_long_code_is_flagged_on_the_sheet(self, client, labels_setup):
        token = labels_setup["token"]
        units = client.get("/api/v1/inventory/units", headers=auth(token)).json()
        piece = next(u for u in units if u["code"] == "PCS")
        client.post(
            "/api/v1/inventory/items",
            headers=auth(token),
            json={
                "name": "طويل",
                "name_ar": "طويل",
                "item_type": "purchased",
                "base_unit_id": piece["id"],
                "barcode": "LONGCODE-0123456789012",
            },
        )
        html = client.get(f"/api/v1{label_path(client, token)}").text
        assert "label--wide" in html
        assert "أطول من الملصق" in html
