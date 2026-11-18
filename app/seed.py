"""Create a demo company with realistic Egyptian data.

Usage: ``python -m app.seed``
Idempotent: does nothing if the demo user already exists.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from .db.models import Company, Contact, User
from .db.session import SessionLocal
from .repositories.core import AccountRepository
from .services.auth import AuthService
from .services.invoices import InvoiceService
from .services.onboarding import provision_company
from .services.payments import PaymentService

DEMO_EMAIL = "admin@msfactory.eg"
DEMO_PASSWORD = "Admin123!@#"


def seed() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.email == DEMO_EMAIL)):
            print("Demo data already present; skipping.")
            return

        auth = AuthService(db)
        user = auth.register(email=DEMO_EMAIL, password=DEMO_PASSWORD, full_name="مدير النظام")
        company = Company(
            name="شركة النور للتجارة",
            legal_name="شركة النور للتجارة والتوزيع",
            tax_number="123-456-789",
            country="EG",
            base_currency="EGP",
        )
        provision_company(db, company=company, owner_id=user.id, role="owner")

        contacts = [
            ("مؤسسة الأمل التجارية", "customer", "01001234567"),
            ("شركة الرياض للمقاولات", "customer", "01109876543"),
            ("مورد الخامات المتحدة", "supplier", "01223344556"),
        ]
        created = {}
        for name, kind, phone in contacts:
            contact = Contact(name=name, kind=kind, phone=phone, tax_number="", payment_terms_days=30)
            contact.company_id = company.id
            db.add(contact)
            db.flush()
            created[name] = contact

        accounts = AccountRepository(db, company.id)
        sales_invoices = InvoiceService(db, company.id, user.id)
        cash = accounts.by_code("1010")
        bank = accounts.by_code("1020")

        sales_invoices.create(
            kind="sales",
            contact_id=created["مؤسسة الأمل التجارية"].id,
            issue_date=date(2026, 10, 1),
            due_date=date(2026, 10, 31),
            lines=[
                {"description": "توريد أجهزة", "quantity": 10, "unit_price": 2500, "tax_rate": 14},
                {"description": "خدمة تركيب", "quantity": 1, "unit_price": 5000, "tax_rate": 14},
            ],
            notes="عرض سعر معتمد",
        )
        sales_invoices.create(
            kind="purchase",
            contact_id=created["مورد الخامات المتحدة"].id,
            issue_date=date(2026, 10, 5),
            due_date=date(2026, 11, 5),
            lines=[{"description": "شراء خامات", "quantity": 100, "unit_price": 100, "tax_rate": 14}],
        )
        payments = PaymentService(db, company.id, user.id)
        payments.create(
            kind="receive",
            contact_id=created["مؤسسة الأمل التجارية"].id,
            payment_date=date(2026, 10, 15),
            amount=Decimal("20000"),
            bank_account_id=bank.id,
            reference="تحويل بنكي",
        )
        payments.create(
            kind="spend",
            contact_id=created["مورد الخامات المتحدة"].id,
            payment_date=date(2026, 10, 20),
            amount=Decimal("5000"),
            bank_account_id=cash.id,
            reference="دفعة نقدية",
        )
        db.commit()
        print(f"Demo ready — {DEMO_EMAIL} / {DEMO_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
