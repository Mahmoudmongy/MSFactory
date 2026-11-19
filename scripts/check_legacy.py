"""Drive the legacy screens in a real browser and report what each renders.

Temporary verification harness: logs in, waits for the bridge to hydrate, then
visits every screen in the navigation map and records the first table row so we
can see whether real company data reached the UI.

Run:  python scripts/check_legacy.py
"""
from __future__ import annotations

import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("LEGACY_BASE", "http://127.0.0.1:12000/app")
EMAIL = os.environ["MSFACTORY_DEMO_EMAIL"]
PASSWORD = os.environ["MSFACTORY_DEMO_PASSWORD"]

SCREENS = [
    ("home", "الملخص"),
    ("bank", "الحسابات النقدية والبنوك"),
    ("rc", "المقبوضات"),
    ("py", "المدفوعات"),
    ("cust", "العملاء"),
    ("supp", "الموردون"),
    ("sale", "فواتير البيع"),
    ("sret", "إشعارات دائنة"),
    ("pur", "فواتير الشراء"),
    ("pret", "إشعارات مدينة"),
    ("stock", "أصناف المخزون"),
    ("prod", "أوامر الإنتاج"),
    ("coa", "شجرة الحسابات"),
    ("je", "قيود اليومية"),
    ("tl", "السجل الزمني"),
    ("emp", "الموظفون"),
    ("rep", "التقارير"),
    ("statements", "القوائم المالية"),
    ("set", "الإعدادات"),
]


def main() -> int:
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{BASE}/auth.html", wait_until="networkidle")
        page.fill("#auth-email", EMAIL)
        page.fill("#auth-password", PASSWORD)
        page.click("#auth-login")
        page.wait_for_url(re.compile(r"index\.html"), timeout=30000)
        page.wait_for_timeout(8000)

        # Confirm the bridge hydrated the state from our API.
        diag = page.evaluate(
            """() => {
                const S = window.__factoryGetState?.() || {};
                return {
                  accts: (S.accts||[]).length,
                  items: (S.items||[]).length,
                  docs: (S.docs||[]).length,
                  L: (S.L||[]).length,
                  partners: (S.partners||[]).length,
                  firstAcctCode: S.accts?.[0]?.code || null,
                  firstItemSku: S.items?.[0]?.sku || null,
                  ready: window.__factoryDatabaseFirstReady === true,
                };
            }"""
        )
        print("HYDRATION:", json.dumps(diag, ensure_ascii=False))

        for sid, label in SCREENS:
            try:
                page.evaluate(f"go('{sid}')")
                page.wait_for_timeout(1200)
                info = page.evaluate(
                    """() => {
                        const main = document.querySelector('#main, .main, main, #content') || document.body;
                        const rows = main.querySelectorAll('table tr');
                        const first = rows.length > 1 ? rows[1].innerText.replace(/\\s+/g,' ').trim() : '';
                        const head = main.querySelector('h1,h2,h3');
                        return {
                          title: head ? head.innerText.trim() : '',
                          rows: rows.length,
                          firstRow: first.slice(0, 90),
                          text: main.innerText.replace(/\\s+/g,' ').trim().slice(0, 120),
                        };
                    }"""
                )
                marker = "OK " if info["rows"] > 1 else "-- "
                print(f"{marker}{sid:11} rows={info['rows']:>3}  {info['firstRow']}")
            except Exception as exc:  # noqa: BLE001 - report and continue
                failures.append(f"{sid}: {exc}")
                print(f"ERR {sid:11} {exc}")

        if errors:
            print("\nCONSOLE ERRORS:")
            for e in errors[:15]:
                print("  -", e[:160])
        browser.close()

    if failures:
        print("\nFAILED:", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
