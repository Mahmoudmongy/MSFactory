"""End-to-end check that a screen save reaches the relational database.

Logs in through the real auth page, drives the real screens, and then reads the
result straight from Postgres — so a green result means the write bridge really
persisted, not just that the local array changed.

Usage::

    MSFACTORY_DEMO_EMAIL=... MSFACTORY_DEMO_PASSWORD=... python scripts/check_write.py
"""
from __future__ import annotations

import os
import re
import sys

from playwright.sync_api import sync_playwright
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db.session import engine  # noqa: E402

BASE = os.environ.get("MSFACTORY_BASE", "http://127.0.0.1:12000")
EMAIL = os.environ["MSFACTORY_DEMO_EMAIL"]
PASSWORD = os.environ["MSFACTORY_DEMO_PASSWORD"]

PARTNER = "عميل اختبار الجسر"
NOTE = "ملاحظة اختبار الجسر"
ITEM = "صنف اختبار الجسر"
ACCOUNT = "حساب اختبار الجسر"
VOUCHER = "سند اختبار الجسر"
JOURNAL = "قيد اختبار الجسر"
DAMAGE = "هالك اختبار الجسر"


def scalar(sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def restore_stock(conn, movement_ids: list[int]) -> None:
    """Undo movements, putting consumed quantities back into their layers.

    Deleting a movement alone leaves the FIFO layer it drew from drained, so
    repeated test runs would silently eat the demo stock.
    """
    if not movement_ids:
        return
    conn.execute(
        text(
            """
            update stock_layers l
            set quantity_remaining = l.quantity_remaining + c.quantity
            from stock_consumptions c
            where c.layer_id = l.id and c.movement_id = any(:ids)
            """
        ),
        {"ids": movement_ids},
    )
    conn.execute(
        text("delete from stock_consumptions where movement_id = any(:ids)"),
        {"ids": movement_ids},
    )
    conn.execute(
        text("delete from stock_layers where movement_id = any(:ids)"), {"ids": movement_ids}
    )
    conn.execute(text("delete from stock_movements where id = any(:ids)"), {"ids": movement_ids})


def cleanup() -> None:
    """Remove the check's rows, children first, so foreign keys stay happy."""
    with engine.begin() as conn:
        invoice_ids = [
            r[0]
            for r in conn.execute(
                text("select id from invoices where notes = :n"), {"n": NOTE}
            ).all()
        ]
        entry_ids = [
            r[0]
            for r in conn.execute(
                text(
                    """
                    select journal_entry_id from invoices
                    where notes = :n and journal_entry_id is not null
                    """
                ),
                {"n": NOTE},
            ).all()
        ]
        numbers = [
            r[0]
            for r in conn.execute(
                text("select number from invoices where notes = :n"), {"n": NOTE}
            ).all()
        ]

        if invoice_ids:
            conn.execute(
                text("delete from payment_allocations where invoice_id = any(:ids)"),
                {"ids": invoice_ids},
            )
            conn.execute(
                text("delete from invoice_lines where invoice_id = any(:ids)"),
                {"ids": invoice_ids},
            )
            conn.execute(
                text("update invoices set journal_entry_id = null where id = any(:ids)"),
                {"ids": invoice_ids},
            )
            conn.execute(text("delete from invoices where id = any(:ids)"), {"ids": invoice_ids})
        if entry_ids:
            conn.execute(
                text("delete from journal_lines where entry_id = any(:ids)"), {"ids": entry_ids}
            )
            conn.execute(
                text("delete from journal_entries where id = any(:ids)"), {"ids": entry_ids}
            )
        if numbers:
            sold_ids = [
                r[0]
                for r in conn.execute(
                    text("select id from stock_movements where reference = any(:refs)"),
                    {"refs": numbers},
                ).all()
            ]
            restore_stock(conn, sold_ids)
        item_ids = [
            r[0]
            for r in conn.execute(
                text("select id from items where name = :n"), {"n": ITEM}
            ).all()
        ]
        if item_ids:
            # A layer created by this test item may have been consumed by any
            # movement, so clear those links before the layers go.
            conn.execute(
                text(
                    """
                    delete from stock_consumptions
                    where layer_id in (select id from stock_layers where item_id = any(:ids))
                       or movement_id in (select id from stock_movements where item_id = any(:ids))
                    """
                ),
                {"ids": item_ids},
            )
            conn.execute(
                text("delete from stock_layers where item_id = any(:ids)"), {"ids": item_ids}
            )
            conn.execute(
                text("delete from stock_movements where item_id = any(:ids)"), {"ids": item_ids}
            )
            conn.execute(
                text("delete from item_variants where item_id = any(:ids)"), {"ids": item_ids}
            )
            conn.execute(text("delete from items where id = any(:ids)"), {"ids": item_ids})
        conn.execute(text("delete from accounts where name = :n"), {"n": ACCOUNT})

        payment_ids = [
            r[0]
            for r in conn.execute(
                text("select id from payments where reference = :n"), {"n": VOUCHER}
            ).all()
        ]
        if payment_ids:
            entry_ids = [
                r[0]
                for r in conn.execute(
                    text(
                        "select journal_entry_id from payments where id = any(:ids) "
                        "and journal_entry_id is not null"
                    ),
                    {"ids": payment_ids},
                ).all()
            ]
            conn.execute(
                text("delete from payment_allocations where payment_id = any(:ids)"),
                {"ids": payment_ids},
            )
            conn.execute(
                text("update payments set journal_entry_id = null where id = any(:ids)"),
                {"ids": payment_ids},
            )
            conn.execute(text("delete from payments where id = any(:ids)"), {"ids": payment_ids})
            if entry_ids:
                conn.execute(
                    text("delete from journal_lines where entry_id = any(:ids)"),
                    {"ids": entry_ids},
                )
                conn.execute(
                    text("delete from journal_entries where id = any(:ids)"), {"ids": entry_ids}
                )

        je_ids = [
            r[0]
            for r in conn.execute(
                text("select id from journal_entries where memo = :n"), {"n": JOURNAL}
            ).all()
        ]
        if je_ids:
            conn.execute(
                text("delete from journal_lines where entry_id = any(:ids)"), {"ids": je_ids}
            )
            conn.execute(
                text("delete from journal_entries where id = any(:ids)"), {"ids": je_ids}
            )

        # Damage write-offs consume a layer and create another, so undo them the
        # same way as a sale before the item rows go.
        damage_movement_ids = [
            r[0]
            for r in conn.execute(
                text(
                    """
                    select id from stock_movements
                    where notes like :n or reference = :r
                    """
                ),
                {"n": f"%{DAMAGE}%", "r": "DMG-SEED"},
            ).all()
        ]
        restore_stock(conn, damage_movement_ids)

        # A write-off also posts an entry whose memo is prefixed with the reason.
        damage_entries = [
            r[0]
            for r in conn.execute(
                text("select id from journal_entries where memo like :n"),
                {"n": f"%{DAMAGE}%"},
            ).all()
        ]
        if damage_entries:
            conn.execute(
                text("delete from journal_lines where entry_id = any(:ids)"),
                {"ids": damage_entries},
            )
            conn.execute(
                text("delete from journal_entries where id = any(:ids)"),
                {"ids": damage_entries},
            )

        # The contact is referenced by journal lines, so it goes last.
        conn.execute(text("delete from contacts where name = :n"), {"n": PARTNER})


def login(page) -> None:
    page.goto(f"{BASE}/app/auth.html", wait_until="networkidle")
    page.fill("#auth-email", EMAIL)
    page.fill("#auth-password", PASSWORD)
    page.click("#auth-login")
    page.wait_for_url(re.compile(r"index\.html"), timeout=30000)
    page.wait_for_function("() => window.__factoryDatabaseFirstReady === true", timeout=30000)


def check_partner(page) -> bool:
    page.evaluate("() => pForm(1)")
    page.wait_for_timeout(500)
    page.fill("#b1", PARTNER)
    page.fill("#b2", "01000000999")
    click_in_form(page, 'حفظ')
    page.wait_for_timeout(4000)
    return bool(scalar("select count(*) from contacts where name = :n", n=PARTNER))


def set_value(page, selector: str, value: str) -> None:
    """Set a field the way a user would, even when a widget hides the control.

    Some selects are replaced by a custom search box, so they are not visible to
    Playwright. Writing ``value`` and dispatching ``input``/``change`` still goes
    through the screen's own listeners, and ``saveDoc`` reads the select value
    directly.
    """
    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            if (!el) throw new Error('missing ' + sel);
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        [selector, value],
    )


def click_in_form(page, label: str, nth: int = 0) -> None:
    """Click a button inside the visible form page, ignoring hidden tabs."""
    buttons = page.locator('#m .fpage:visible').last.get_by_role('button', name=label, exact=True)
    buttons.nth(nth).click()


def check_invoice(page) -> bool:
    """Issue a one-line sales invoice against the partner created above."""
    page.evaluate("() => docForm('sale')")
    page.wait_for_timeout(700)

    partner_id = page.evaluate(
        """(name) => (window.__factoryGetState().partners || [])
              .find((p) => p.name === name)?.id""",
        PARTNER,
    )
    if not partner_id:
        return False
    set_value(page, "#pt", str(partner_id))
    page.evaluate("() => fillIt()")
    page.wait_for_timeout(300)

    first_item = page.evaluate("() => (window.__factoryGetState().items||[])[0]?.id")
    if not first_item:
        return False
    set_value(page, "#it", str(first_item))
    page.evaluate("() => unitSet()")
    page.fill("#q", "2")
    click_in_form(page, '+')
    page.wait_for_timeout(400)
    page.fill("#dn", NOTE)
    click_in_form(page, 'حفظ')
    page.wait_for_timeout(5000)
    return bool(scalar("select count(*) from invoices where notes = :n", n=NOTE))


def check_item(page) -> bool:
    """Create a stock item through its screen and confirm the row landed."""
    page.evaluate("() => itemForm(0)")
    page.wait_for_timeout(600)
    page.fill("#a1", ITEM)
    page.fill("#a2", "TST-BRG-1")
    page.fill("#a3", "90")
    page.fill("#a4", "250")
    click_in_form(page, 'حفظ')
    page.wait_for_timeout(4000)
    return bool(scalar("select count(*) from items where name = :n", n=ITEM))


def check_account(page) -> bool:
    """Add a leaf account to the chart of accounts from its screen."""
    page.evaluate("() => accForm()")
    page.wait_for_timeout(600)
    page.fill("#bn", ACCOUNT)
    page.fill("#bc", "5977")
    click_in_form(page, 'حفظ')
    page.wait_for_timeout(4000)
    return bool(scalar("select count(*) from accounts where name = :n", n=ACCOUNT))


def check_damage(page) -> bool:
    """Write off damaged stock from the screen and confirm the movement row."""
    item_id = scalar("select id from items where name = :n", n=ITEM)
    if not item_id:
        return False

    # The write-off needs stock on hand, so receive some through the API first.
    seeded = page.evaluate(
        """async (itemId) => {
            const token = localStorage.getItem('msfactory.legacy.access');
            const warehouseId = (window.__factoryGetState().wh[0] || {}).id;
            const response = await fetch('/api/v1/inventory/movements/receive', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
              body: JSON.stringify({
                item_id: itemId, warehouse_id: warehouseId, quantity: 10, unit_cost: 90,
                movement_date: new Date().toISOString().slice(0, 10),
                reference: 'DMG-SEED',
              }),
            });
            if (!response.ok) return false;
            await window.__factoryDatabaseSyncNow();
            return true;
        }""",
        item_id,
    )
    if not seeded:
        return False
    page.wait_for_timeout(1500)

    page.evaluate("() => woForm()")
    page.wait_for_timeout(700)
    set_value(page, "#wi", str(item_id))
    page.evaluate("() => wvr()")
    page.fill("#wq", "3")
    page.fill("#wd", DAMAGE)
    click_in_form(page, 'حفظ')
    page.wait_for_timeout(5000)
    return bool(scalar("select count(*) from stock_movements where notes like :n", n=f"%{DAMAGE}%"))


def check_journal(page) -> bool:
    """Post a two-line manual journal entry from its screen."""
    page.evaluate("() => jForm({})")
    page.wait_for_timeout(700)

    accounts = page.evaluate(
        "() => (window.__factoryGetState().accts || []).filter((a) => !a.isGroup).map((a) => a.id)"
    )
    if len(accounts) < 2:
        return False

    page.fill("#j0", JOURNAL)
    for index, account_id in enumerate(accounts[:2]):
        set_value(page, "#j1", str(account_id))
        page.fill("#j2" if index == 0 else "#j3", "300")
        click_in_form(page, '+')
        page.wait_for_timeout(200)

    click_in_form(page, 'حفظ')
    page.wait_for_timeout(5000)
    return bool(scalar("select count(*) from journal_entries where memo = :n", n=JOURNAL))


def check_voucher(page) -> bool:
    """Record a cash receipt against the partner and confirm the payment row."""
    page.evaluate("() => vForm('in')")
    page.wait_for_timeout(700)

    cash_id = page.evaluate(
        "() => (window.__factoryGetState().accts || []).find((a) => a.cash)?.id"
    )
    partner_id = page.evaluate(
        """(name) => (window.__factoryGetState().partners || [])
              .find((p) => p.name === name)?.id""",
        PARTNER,
    )
    if not cash_id or not partner_id:
        return False

    set_value(page, "#va", str(cash_id))
    page.fill("#vm", "500")
    click_in_form(page, '+')
    page.wait_for_timeout(300)

    # Balance the voucher with a party line (the form refuses an unbalanced one).
    set_value(page, "#la", "p")
    page.fill("#lm", "500")
    click_in_form(page, '+', nth=1)
    page.wait_for_timeout(300)

    set_value(page, "#vt", "c")
    page.evaluate("() => vpt()")
    page.wait_for_timeout(300)
    set_value(page, "#vp", str(partner_id))

    # A new voucher defaults to invoice linking; this receipt is on account.
    page.uncheck("#lkc")
    page.evaluate("() => lkTog()")

    # Use an explicit reference so the row is easy to find and clean up.
    page.uncheck("#v0")
    page.fill("#vr", VOUCHER)
    page.fill("#vx", VOUCHER)
    click_in_form(page, 'حفظ')
    page.wait_for_timeout(5000)
    return bool(scalar("select count(*) from payments where reference = :n", n=VOUCHER))


def main() -> int:
    cleanup()
    results: dict[str, bool] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        login(page)
        results["partner"] = check_partner(page)
        results["item"] = check_item(page)
        results["account"] = check_account(page)
        results["journal"] = check_journal(page)
        results["damage"] = check_damage(page)
        results["voucher"] = check_voucher(page)
        results["invoice"] = check_invoice(page)

        if errors:
            print("PAGE ERRORS:", errors[:5])
        browser.close()

    cleanup()
    for name, ok in results.items():
        print(f"{'OK ' if ok else 'FAIL'} {name}")
    return 0 if all(results.values()) and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

