"""Seed the standard chart of accounts, default settings and demo user."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, Setting, TaxCode, User
from .security import hash_password

# code, name (en), name (ar), type, parent code
CHART = [
    ("1000", "Assets", "الأصول", "asset", None),
    ("1100", "Accounts Receivable", "العملاء (مدينون)", "asset", "1000"),
    ("1200", "Cash on Hand", "النقدية بالصندوق", "asset", "1000"),
    ("1210", "Bank Account", "الحساب البنكي", "asset", "1000"),
    ("1300", "Inventory", "المخزون", "asset", "1000"),
    ("1400", "Fixed Assets", "الأصول الثابتة", "asset", "1000"),
    ("2000", "Liabilities", "الالتزامات", "liability", None),
    ("2100", "Accounts Payable", "الموردون (دائنون)", "liability", "2000"),
    ("2200", "VAT Payable", "ضريبة القيمة المضافة", "liability", "2000"),
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
    ("5900", "General Expenses", "مصروفات عمومية", "expense", "5000"),
]

DEFAULTS = {
    "company_name": "شركتي / My Company",
    "currency": "EGP",
    "vat_rate": "14",
    "fiscal_year_start": "01-01",
}


def seed(db: Session) -> None:
    if db.scalar(select(Account).limit(1)):
        return

    by_code: dict[str, Account] = {}
    for code, name, name_ar, type_, parent in CHART:
        account = Account(
            code=code,
            name=name,
            name_ar=name_ar,
            type=type_,
            is_group=parent is None,
            parent_id=by_code[parent].id if parent else None,
        )
        db.add(account)
        db.flush()
        by_code[code] = account

    db.add(TaxCode(name="VAT 14%", rate=14, account_id=by_code["2200"].id))
    db.add(TaxCode(name="Zero Rated", rate=0, account_id=None))

    for key, value in DEFAULTS.items():
        db.add(Setting(key=key, value=value))

    db.add(
        User(
            username="admin",
            full_name="مدير النظام",
            password_hash=hash_password("admin123"),
            role="admin",
        )
    )
    db.commit()
