#!/usr/bin/env python3
"""Seed one demo company with a realistic month so the UI can be judged on real
numbers rather than an empty screen.

Run:  python scripts/seed_demo.py
Prints the login it created. Safe to run repeatedly: each run registers a new
company, so nothing is overwritten.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

BASE = "http://127.0.0.1:12000/api/v1"
TODAY = datetime.now(UTC).date()


def day(offset: int) -> str:
    """Date string for `offset` days from today, for readable seed data."""
    return str(TODAY + timedelta(days=offset))


def call(method: str, path: str, token: str | None = None, **body):
    data = json.dumps(body).encode() if body else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode()
        raise SystemExit(f"{method} {path} -> {error.code}\n{detail}") from None


def main() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    email = f"demo.{stamp}@msfactory.eg"
    account = call(
        "POST",
        "/auth/register",
        email=email,
        password="Str0ng!Pass",
        full_name="مدير الشركة",
        company_name="مصنع النور للأثاث",
    )
    token = account["access_token"]
    print(f"login: {email} / Str0ng!Pass")

    # --- master data -------------------------------------------------
    # Units are provisioned at registration, so reuse the seeded one rather than
    # creating a duplicate that the API would reject.
    unit = next(u for u in call("GET", "/inventory/units", token) if u["code"] == "PCS")
    warehouse = call(
        "POST", "/inventory/warehouses", token, code="WH-MAIN", name="Main", name_ar="المخزن الرئيسي"
    )
    showroom = call(
        "POST",
        "/inventory/warehouses",
        token,
        code="WH-SHOW",
        name="Showroom",
        name_ar="معرض البيع",
        parent_id=warehouse["id"],
    )
    stock_wh = showroom["id"]  # a group warehouse cannot hold stock, only its child can

    items = {}
    for code, name, purchase, sale in [
        ("CHR-01", "كرسي خشب زان", "450.00", "750.00"),
        ("TBL-01", "طاولة سفرة ٦ كراسي", "3200.00", "5200.00"),
        ("DRS-01", "دولاب ٣ ضلفة", "2400.00", "3900.00"),
        ("BED-01", "سرير ١٦٠ سم", "4100.00", "6500.00"),
        ("SHF-01", "شلفة حائط", "300.00", "550.00"),
    ]:
        items[code] = call(
            "POST",
            "/inventory/items",
            token,
            code=code,
            name=name,
            name_ar=name,
            item_type="stock",
            base_unit_id=unit["id"],
            default_warehouse_id=stock_wh,
            purchase_price=purchase,
            sale_price=sale,
            track_inventory=True,
            reorder_level=10,
        )

    customers = {}
    for name, phone in [
        ("مكتب خوليو للأثاث", "01001234567"),
        ("شركة الهرم للتوريدات", "01109876543"),
        ("فندق النيل بلازا", "01223334444"),
    ]:
        customers[name] = call("POST", "/contacts", token, name=name, kind="customer", phone=phone)

    supplier = call("POST", "/contacts", token, name="مصنع الأخشاب الوطني", kind="supplier", phone="01005556666")

    # --- opening cash, so the demo starts with a positive till ---------
    accounts = {a["code"]: a for a in call("GET", "/accounts", token)}
    capital = accounts["3100"]
    cash = accounts["1010"]
    call(
        "POST",
        "/journal",
        token,
        entry_date=day(-30),
        memo="رصيد افتتاحي — النقدية ورأس المال",
        reference="OPEN-CASH",
        lines=[
            {"account_id": cash["id"], "debit": "60000.00", "credit": "0"},
            {"account_id": capital["id"], "debit": "0", "credit": "60000.00"},
        ],
    )

    # --- opening stock (received at purchase cost) --------------------
    for code, quantity in [("CHR-01", 120), ("TBL-01", 30), ("DRS-01", 40), ("BED-01", 25), ("SHF-01", 80)]:
        item = items[code]
        call(
            "POST",
            "/inventory/movements/receive",
            token,
            item_id=item["id"],
            warehouse_id=stock_wh,
            quantity=quantity,
            unit_cost=item["purchase_price"],
            movement_date=day(-25),
            reference="رصيد افتتاحي",
        )

    # --- sales invoices, issued (they post to the ledger) -------------
    sales = [
        ("مكتب خوليو للأثاث", [("CHR-01", 12), ("TBL-01", 2)], 8),
        ("شركة الهرم للتوريدات", [("DRS-01", 5), ("BED-01", 3)], 6),
        ("فندق النيل بلازا", [("TBL-01", 6), ("CHR-01", 30), ("SHF-01", 10)], 4),
        ("مكتب خوليو للأثاث", [("SHF-01", 6), ("DRS-01", 2)], 2),
    ]
    for customer_name, lines, days_ago in sales:
        payload = [
            {
                "item_id": items[code]["id"],
                "warehouse_id": stock_wh,
                "quantity": quantity,
                "unit_price": items[code]["sale_price"],
                "tax_rate": "14",
            }
            for code, quantity in lines
        ]
        call(
            "POST",
            "/invoices",
            token,
            kind="sales",
            contact_id=customers[customer_name]["id"],
            issue_date=day(-days_ago),
            due_date=day(30 - days_ago),
            lines=payload,
        )

    # --- a purchase invoice from the supplier -------------------------
    call(
        "POST",
        "/invoices",
        token,
        kind="purchase",
        contact_id=supplier["id"],
        issue_date=day(-20),
        lines=[
            {
                "item_id": items["CHR-01"]["id"],
                "warehouse_id": stock_wh,
                "quantity": 40,
                "unit_price": "450.00",
                "tax_rate": "14",
            },
            {
                "item_id": items["SHF-01"]["id"],
                "warehouse_id": stock_wh,
                "quantity": 30,
                "unit_price": "300.00",
                "tax_rate": "14",
            },
        ],
    )

    # --- collections and a payment ------------------------------------
    bank = next(a for a in call("GET", "/accounts", token) if a["code"] == "1010")
    call(
        "POST",
        "/payments",
        token,
        kind="receive",
        contact_id=customers["مكتب خوليو للأثاث"]["id"],
        bank_account_id=bank["id"],
        amount="9000.00",
        date=day(-1),
        reference="تحصيل نقدي",
    )
    call(
        "POST",
        "/payments",
        token,
        kind="spend",
        contact_id=supplier["id"],
        bank_account_id=bank["id"],
        amount="12000.00",
        date=day(-1),
        reference="سداد دفعة",
    )

    # --- one production order ----------------------------------------
    order = call(
        "POST",
        "/production/work-orders",
        token,
        item_id=items["TBL-01"]["id"],
        warehouse_id=stock_wh,
        quantity=5,
        start_date=day(-3),
        due_date=day(4),
        materials=[
            {"item_id": items["CHR-01"]["id"], "quantity": 4},
            {"item_id": items["SHF-01"]["id"], "quantity": 2},
        ],
    )
    call("POST", f"/production/work-orders/{order['id']}/start", token)

    # --- a cash sub-account, to show the cash screen tree -------------
    bank_root = next(a for a in call("GET", "/accounts", token) if a["code"] == "1020")
    call(
        "POST",
        "/accounts",
        token,
        code="1021",
        name="بنك مصر — جاري",
        name_ar="بنك مصر — جاري",
        type="asset",
        parent_id=bank_root["id"],
    )

    print(f"company: مصنع النور للأثاث ({email})")
    print("done")


if __name__ == "__main__":
    sys.exit(main())
