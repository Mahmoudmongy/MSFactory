"""The alert sheet must show a movement's details and open that movement.

These are static checks against the shipped JavaScript and CSS: the sheet is
built at runtime by dashboard.js, and a regression here is silent — the dialog
still opens, it just stops carrying the details or the tap-through.
"""
from __future__ import annotations

from pathlib import Path

import pytest

LEGACY = Path(__file__).resolve().parent.parent / "legacy"
DASHBOARD_JS = LEGACY / "js" / "dashboard.js"
DASHBOARD_CSS = LEGACY / "css" / "dashboard.css"
APP_JS = LEGACY / "js" / "app.js"
STYLE_CSS = LEGACY / "css" / "style.css"


@pytest.fixture(scope="module")
def dashboard() -> str:
    return DASHBOARD_JS.read_text(encoding="utf-8")


def test_every_alert_kind_carries_detail_fields(dashboard: str) -> None:
    """A count alone never told the user which document to act on."""
    start = dashboard.index("function alertRows(")
    end = dashboard.index("const ALERT_TITLES")
    body = dashboard[start:end]

    # One `fields:` block per alert kind.
    assert body.count("fields: [") == 3, "overdue, low-stock and open-orders each need fields"
    for key in ("overdue-sales", "low-stock", "open-orders"):
        assert key in body


def test_each_alert_row_opens_its_own_record(dashboard: str) -> None:
    start = dashboard.index("function alertRows(")
    body = dashboard[start : dashboard.index("const ALERT_TITLES")]

    assert "open: () => openRef('doc', d.id)" in body
    # The work-order callback is wrapped in a typeof guard, so match loosely.
    assert "openRef('po', o.id)" in body


def test_sheet_renders_the_fields_and_a_tap_through(dashboard: str) -> None:
    start = dashboard.index("function openAlertSheet(")
    body = dashboard[start : dashboard.index("document.addEventListener('keydown'")]

    assert "factory-sheet-fields" in body
    assert "factory-sheet-open" in body
    # The row button is what carries data-row, so the whole row is tappable.
    assert 'data-row="' in body
    assert "target.open()" in body


def test_a_malformed_imported_order_cannot_blank_the_sheet(dashboard: str) -> None:
    """poQty reads `subs`, which imported Manager.io orders can lack."""
    start = dashboard.index("function alertRows(")
    body = dashboard[start : dashboard.index("const ALERT_TITLES")]

    assert "try {" in body
    assert "catch (_)" in body


@pytest.mark.parametrize("selector", [".factory-sheet-fields", ".factory-sheet-open"])
def test_sheet_detail_styles_exist(selector: str) -> None:
    assert selector in DASHBOARD_CSS.read_text(encoding="utf-8")


def test_phone_touch_targets_are_at_least_44px() -> None:
    css = DASHBOARD_CSS.read_text(encoding="utf-8")
    phone = css[css.index("@media(max-width:860px)"):]

    assert "min-height:44px" in phone


def test_assistant_prompts_are_a_grid_not_bare_inline_buttons() -> None:
    html = APP_JS.read_text(encoding="utf-8")
    assert 'class="ai-prompts" role="group"' in html
    # Each prompt is its own card so they wrap evenly instead of ragged.
    assert html.count('class="g ai-prompt"') == 4

    css = STYLE_CSS.read_text(encoding="utf-8")
    assert ".ai-prompts{" in css.replace(" ", "")
    assert "grid-template-columns" in css
    assert "min-height:44px" in css
