"""Reset the database and optionally load demo data.

Usage:
    python -m app.reset          # wipe everything, keep chart of accounts + admin
    python -m app.reset --demo   # wipe everything and add sample customers/invoices
"""
import sys
from datetime import date

from sqlalchemy import select

from .accounting import post_entry
from .database import Base, SessionLocal, engine
from .models import (
    Account,
    Contact,
    Invoice,
    InvoiceLine,
    JournalEntry,
    JournalLine,
    Payment,
    Setting,
    TaxCode,
    User,
)
from .seed import seed

WIPE_ORDER = (
    JournalLine,
    JournalEntry,
    Payment,
    InvoiceLine,
    Invoice,
    Contact,
    Account,
    TaxCode,
    Setting,
    User,
)


def _code(db, code: str) -> Account:
    return db.scalar(select(Account).where(Account.code == code))


def demo(db) -> None:
    customers = [
        Contact(name="شركة النور للتجارة", kind="customer", phone="01000000000"),
        Contact(name="مؤسسة الوفاء", kind="customer", phone="01111111111"),
        Contact(name="مورد الأجهزة الحديثة", kind="supplier", phone="01222222222"),
    ]
    db.add_all(customers)
    db.flush()

    sales = _code(db, "4100")
    cogs = _code(db, "5100")
    ar = _code(db, "1100")
    ap = _code(db, "2100")
    vat = _code(db, "2200")

    inv1 = Invoice(
        kind="sales", number="INV-00001", contact_id=customers[0].id, issue_date=date(2026, 10, 1),
        status="issued", subtotal=30000, tax_total=4200, total=34200,
    )
    inv1.lines.append(InvoiceLine(description="أجهزة لابتوب", quantity=2, unit_price=15000, tax_rate=14, account_id=sales.id))
    db.add(inv1)
    db.flush()
    post_entry(db, inv1.issue_date, [
        {"account_id": sales.id, "credit": 30000},
        {"account_id": ar.id, "contact_id": customers[0].id, "debit": 34200},
        {"account_id": vat.id, "credit": 4200},
    ], memo="Sales invoice INV-00001", reference="INV-00001", source_type="invoice", source_id=inv1.id)

    inv2 = Invoice(
        kind="purchase", number="BILL-00001", contact_id=customers[2].id, issue_date=date(2026, 10, 5),
        status="issued", subtotal=10000, tax_total=1400, total=11400,
    )
    inv2.lines.append(InvoiceLine(description="مخزون", quantity=20, unit_price=500, tax_rate=14, account_id=cogs.id))
    db.add(inv2)
    db.flush()
    post_entry(db, inv2.issue_date, [
        {"account_id": cogs.id, "debit": 10000},
        {"account_id": ap.id, "contact_id": customers[2].id, "credit": 11400},
        {"account_id": vat.id, "debit": 1400},
    ], memo="Purchase BILL-00001", reference="BILL-00001", source_type="invoice", source_id=inv2.id)

    db.commit()
    print("Demo data loaded.")


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for model in WIPE_ORDER:
            db.query(model).delete()
        db.commit()
    with SessionLocal() as db:
        seed(db)
        if "--demo" in sys.argv:
            demo(db)
    print("Database reset. Login: admin / admin123")


if __name__ == "__main__":
    main()
