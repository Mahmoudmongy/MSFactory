"""Company onboarding: create a tenant, its chart of accounts and settings.

Called when a user signs up. The chart of accounts is Egypt-oriented by
default (EGP, 14% VAT) but every value lands in editable rows, so a deployment
in another country only changes data.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ..db.models import Company, CompanySetting, Membership, TaxCode
from ..repositories.core import AccountRepository, SequenceRepository

# code, english name, arabic name, type, parent code
CHART_OF_ACCOUNTS = [
    ("1000", "Assets", "الأصول", "asset", None),
    ("1010", "Cash on Hand", "النقدية بالصندوق", "asset", "1000"),
    ("1020", "Bank Account", "الحساب البنكي", "asset", "1000"),
    ("1100", "Accounts Receivable", "العملاء (مدينون)", "asset", "1000"),
    ("1200", "Inventory", "المخزون", "asset", "1000"),
    ("1300", "VAT Receivable", "ضريبة القيمة المضافة (مدخلات)", "asset", "1000"),
    ("1400", "Fixed Assets", "الأصول الثابتة", "asset", "1000"),
    ("1500", "Accumulated Depreciation", "مجمع الإهلاك", "asset", "1000"),
    ("2000", "Liabilities", "الالتزامات", "liability", None),
    ("2100", "Accounts Payable", "الموردون (دائنون)", "liability", "2000"),
    ("2200", "VAT Payable", "ضريبة القيمة المضافة (مخرجات)", "liability", "2000"),
    ("2300", "Withholding Tax Payable", "ضريبة الخصم والإضافة", "liability", "2000"),
    ("2400", "Payroll Payable", "أجور مستحقة", "liability", "2000"),
    ("3000", "Equity", "حقوق الملكية", "equity", None),
    ("3100", "Owner's Capital", "رأس المال", "equity", "3000"),
    ("3200", "Retained Earnings", "الأرباح المحتجزة", "equity", "3000"),
    ("4000", "Revenue", "الإيرادات", "income", None),
    ("4100", "Sales", "المبيعات", "income", "4000"),
    ("4200", "Other Income", "إيرادات أخرى", "income", "4000"),
    ("5000", "Expenses", "المصروفات", "expense", None),
    ("5100", "Cost of Goods Sold", "تكلفة البضاعة المباعة", "expense", "5000"),
    ("5200", "Salaries", "الرواتب والأجور", "expense", "5000"),
    ("5300", "Rent", "الإيجارات", "expense", "5000"),
    ("5400", "Utilities", "المرافق والخدمات", "expense", "5000"),
    ("5500", "Depreciation Expense", "مصروف الإهلاك", "expense", "5000"),
    ("5900", "General Expenses", "مصروفات عمومية", "expense", "5000"),
]

DEFAULT_SETTINGS = {
    "company_name": "",
    "currency": "EGP",
    "vat_rate": "14",
    "fiscal_year_start": "01-01",
    "invoice_terms_days": "30",
    "invoice_footer": "",
}

TAX_CODES = [
    ("VAT 14%", "ضريبة قيمة مضافة 14%", Decimal("14"), "vat", "2200"),
    ("VAT 5%", "ضريبة قيمة مضافة 5%", Decimal("5"), "vat", "2200"),
    ("Zero Rated", "معفاة (صفرية)", Decimal("0"), "vat", None),
    ("Exempt", "معفاة", Decimal("0"), "vat", None),
    ("WHT 1%", "خصم وإضافة 1%", Decimal("1"), "withholding", "2300"),
    ("WHT 3%", "خصم وإضافة 3%", Decimal("3"), "withholding", "2300"),
]


def provision_company(
    db: Session,
    *,
    company: Company,
    owner_id: int,
    role: str = "owner",
) -> Company:
    """Create a company's chart of accounts, tax codes, settings and owner."""
    from ..db.models import Account

    db.add(company)
    db.flush()

    accounts = AccountRepository(db, company.id)
    by_code: dict[str, Account] = {}
    for code, name, name_ar, type_, parent in CHART_OF_ACCOUNTS:
        account = Account(
            company_id=company.id,
            code=code,
            name=name,
            name_ar=name_ar,
            type=type_,
            is_group=parent is None,
            parent_id=by_code[parent].id if parent else None,
            currency=company.base_currency,
        )
        accounts.add(account)
        by_code[code] = account

    for name, name_ar, rate, kind, account_code in TAX_CODES:
        db.add(
            TaxCode(
                company_id=company.id,
                name=name,
                name_ar=name_ar,
                rate=rate,
                kind=kind,
                account_id=by_code[account_code].id if account_code else None,
            )
        )

    settings_map = dict(DEFAULT_SETTINGS)
    settings_map["company_name"] = company.name
    settings_map["currency"] = company.base_currency
    for key, value in settings_map.items():
        db.add(CompanySetting(company_id=company.id, key=key, value=value))

    db.add(Membership(user_id=owner_id, company_id=company.id, role=role))

    # Pre-create document sequences so numbers are stable from day one.
    sequences = SequenceRepository(db, company.id)
    sequences.next_number("invoice_sales", prefix="INV", padding=5)
    db.flush()
    db.commit()
    return company
