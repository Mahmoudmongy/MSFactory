"""The showcase page is the handover artefact: it must stay reachable and keep
pointing at the screens the reference defines."""
from __future__ import annotations

from pathlib import Path

SHOWCASE = Path(__file__).resolve().parents[1] / "static" / "showcase.html"


def test_showcase_route_serves_the_page(client):
    response = client.get("/showcase")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_showcase_covers_every_navigation_screen():
    html = SHOWCASE.read_text()
    for key in (
        "dashboard", "accounts", "contacts", "payments", "invoices", "inventory",
        "production", "ledger", "reports", "statements", "journal", "team",
        "assistant", "timeline", "settings",
    ):
        assert f'"#/{key}"' in html or '"/#/" + key' in html
