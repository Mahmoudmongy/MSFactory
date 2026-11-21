"""New documents must open with empty fields, not with example values.

A field that arrives holding ``1``, ``0`` or the first customer in the list is
a real value: if the user does not overwrite it, it is what gets saved. These
checks pin the empty-field rule on the shipped JavaScript so a later edit
cannot quietly reintroduce a default that ends up in the books.

Defaults that the server applies anyway (today's date, the company tax rate)
are deliberate and are asserted here too, so the rule is not over-applied.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "static" / "app.js"
STYLES_CSS = Path(__file__).resolve().parent.parent / "static" / "styles.css"


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The whole function, counted from the brace that opens its body.

    Counting from the first ``{`` after the name would stop early on a
    destructured parameter such as ``({ invoiceId } = {})``.
    """
    start = source.index(signature)
    params_open = source.index("(", start)
    depth = 0
    for index in range(params_open, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                body_start = source.index("{", index)
                break
    else:
        raise AssertionError(f"unbalanced parameters after {signature}")

    depth = 0
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after {signature}")


def test_invoice_line_fields_open_empty(app_js: str) -> None:
    body = _function_body(app_js, "async function renderInvoiceForm")
    for field in ("data-quantity", "data-unit-price", "data-discount"):
        match = re.search(rf'data-{field.split("data-")[1]}[^>]*>', body)
        assert match, f"{field} input is missing from the invoice form"
        tag = match.group(0)
        assert 'value="${prefill ?' in tag or 'value=""' in tag, tag
        assert "placeholder=" in tag, f"{field} needs a hint instead of a value"


def test_invoice_line_fields_show_a_hint(app_js: str) -> None:
    body = _function_body(app_js, "async function renderInvoiceForm")
    assert 'placeholder="1"' in body  # quantity
    assert 'placeholder="0.00"' in body  # unit price


def test_invoice_selects_are_not_preselected(app_js: str) -> None:
    """Item, warehouse and tax start on a blank option, not the first row."""
    body = _function_body(app_js, "async function renderInvoiceForm")
    for marker in ('data-item', 'data-warehouse', 'data-tax-rate'):
        start = body.index(marker)
        block = body[start : start + 700]
        assert '<option value="">' in block, f"{marker} must start on a blank option"


def test_an_untouched_tax_field_still_applies_the_company_rate(app_js: str) -> None:
    """Blank means "the default", not "no tax" — dropping VAT understates the bill."""
    body = _function_body(app_js, "async function renderInvoiceForm")
    assert "value === \"\"" in body
    assert "? defaultRate" in body


def test_an_empty_quantity_counts_as_one(app_js: str) -> None:
    body = _function_body(app_js, "async function renderInvoiceForm")
    assert 'Number(node.querySelector("[data-quantity]").value) || 1' in body


def test_stock_movement_starts_with_no_item_or_warehouse(app_js: str) -> None:
    body = _function_body(app_js, "function openMovementSheet")
    assert "BLANK_ITEM" in body and "BLANK_WH" in body
    assert "if (!itemId) return toast" in body
    assert 'if (!Number(data.get("warehouse_id"))) return toast' in body


def test_journal_lines_start_without_an_account(app_js: str) -> None:
    body = _function_body(app_js, "async function renderJournalForm")
    assert "blankAccount" in body
    assert "if (lines.some((line) => !line.account_id))" in body


def test_placeholders_are_styled_readable() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    assert "::placeholder" in css
    block = css[css.index("::placeholder") :][:400]
    assert "var(--ink-500)" in block
    assert "opacity: 1" in block  # Firefox dims placeholders by default


def test_placeholder_contrast_meets_wcag_aa() -> None:
    """--ink-500 must stay above 4.5:1 on both the light and dark surface."""

    def luminance(hex_color: str) -> float:
        value = hex_color.lstrip("#")
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def contrast(a: str, b: str) -> float:
        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    css = STYLES_CSS.read_text(encoding="utf-8")
    ink500 = re.findall(r"--ink-500:\s*(#[0-9a-fA-F]{6})", css)
    surface = re.findall(r"--surface:\s*(#[0-9a-fA-F]{6})", css)
    assert len(ink500) == 2 and len(surface) == 2, "expected one light and one dark theme"
    for ink, background in zip(ink500, surface, strict=False):
        assert contrast(ink, background) >= 4.5, f"{ink} on {background} is too faint"
