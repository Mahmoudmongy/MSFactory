"""The catalogue of reports: one list that drives the API, the Excel export and
the on-screen menu.

Keeping the catalogue here rather than as a set of decorators means the
endpoints, the export and the client menu cannot drift apart. Adding a report is
one entry, and it appears everywhere — including as an Excel export, because the
export endpoint looks the key up in this same list rather than repeating it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportSpec:
    """One report: how to call it, what to call it, and what it needs.

    ``method`` is the ``ReportService`` method name. ``params`` names the query
    arguments the report accepts, so the API and the exporter pass exactly the
    right ones and nothing else.
    """

    key: str
    label: str
    category: str
    method: str
    icon: str = "📄"
    params: tuple[str, ...] = ()
    #: Params that arrive as a comma-separated list rather than one value.
    list_params: tuple[str, ...] = ()
    description: str = ""


#: The categories, in the order the menu shows them.
CATEGORIES = (
    ("overview", "نظرة عامة"),
    ("statements", "القوائم المالية"),
    ("ledger", "الدفاتر والأستاذ"),
    ("parties", "العملاء والموردون"),
    ("inventory", "المخزون"),
    ("sales", "المبيعات والمشتريات"),
    ("tax", "الضرائب"),
    ("custom", "تقارير مخصصة"),
)


REPORTS: tuple[ReportSpec, ...] = (
    # ------------------------------------------------------------ overview
    ReportSpec(
        key="dashboard",
        label="لوحة المعلومات",
        category="overview",
        method="dashboard",
        icon="🏠",
        description="النقد والأرباح والمستحقات في شاشة واحدة",
    ),
    ReportSpec(
        key="kpis",
        label="المؤشرات الرئيسية",
        category="overview",
        method="kpis",
        icon="📈",
        params=("days",),
        description="أرقام الفترة مقارنة بالفترة السابقة",
    ),
    ReportSpec(
        key="alerts",
        label="التنبيهات",
        category="overview",
        method="alerts",
        icon="🔔",
        description="فواتير متأخرة ومخزون يحتاج إعادة طلب",
    ),
    # ---------------------------------------------------------- statements
    ReportSpec(
        key="income-statement",
        label="قائمة الدخل",
        category="statements",
        method="income_statement",
        icon="💹",
        params=("start", "end"),
        description="الإيرادات وتكلفة المبيعات والمصروفات وصافي الربح",
    ),
    ReportSpec(
        key="balance-sheet",
        label="الميزانية العمومية",
        category="statements",
        method="balance_sheet",
        icon="⚖️",
        params=("upto",),
        description="الأصول والالتزامات وحقوق الملكية",
    ),
    ReportSpec(
        key="trial-balance",
        label="ميزان المراجعة",
        category="statements",
        method="trial_balance",
        icon="📋",
        params=("start", "upto"),
        description="أرصدة كل الحسابات مديناً ودائناً",
    ),
    ReportSpec(
        key="cash-flow",
        label="التدفق النقدي",
        category="statements",
        method="cash_flow",
        icon="💵",
        params=("start", "end"),
        description="من أين جاء النقد وأين ذهب",
    ),
    # -------------------------------------------------------------- ledger
    ReportSpec(
        key="journal",
        label="دفتر اليومية",
        category="ledger",
        method="journal_book",
        icon="📒",
        params=("start", "end", "limit"),
        description="كل القيود ببنودها",
    ),
    ReportSpec(
        key="general-ledger",
        label="دفتر الأستاذ العام",
        category="ledger",
        method="general_ledger",
        icon="📚",
        params=("start", "end"),
        description="حركات كل حساب ورصيده",
    ),
    ReportSpec(
        key="account-balances",
        label="أرصدة الحسابات",
        category="ledger",
        method="account_balances",
        icon="🧮",
        description="كل حساب ورصيده الحالي",
    ),
    # ------------------------------------------------------------- parties
    ReportSpec(
        key="ar-aging",
        label="أعمار ديون العملاء",
        category="parties",
        method="ar_aging",
        icon="⏳",
        params=("upto",),
        description="من يدين لك وبكم ومنذ متى",
    ),
    ReportSpec(
        key="ap-aging",
        label="أعمار ديون الموردين",
        category="parties",
        method="ap_aging",
        icon="⌛",
        params=("upto",),
        description="لمن تدين وبكم ومنذ متى",
    ),
    ReportSpec(
        key="customer-balances",
        label="أرصدة العملاء",
        category="parties",
        method="customer_balances",
        icon="👥",
        params=("upto",),
        description="رصيد كل عميل وحدّه الائتماني",
    ),
    ReportSpec(
        key="supplier-balances",
        label="أرصدة الموردين",
        category="parties",
        method="supplier_balances",
        icon="🚚",
        params=("upto",),
        description="رصيد كل مورد",
    ),
    ReportSpec(
        key="top-customers",
        label="أفضل العملاء",
        category="parties",
        method="top_customers",
        icon="🏆",
        params=("start", "end", "limit"),
        description="العملاء مرتبين حسب الربح الذي جلبوه",
    ),
    # ----------------------------------------------------------- inventory
    ReportSpec(
        key="stock-valuation",
        label="تقييم المخزون",
        category="inventory",
        method="stock_valuation",
        icon="🏷️",
        params=("warehouse_id", "as_of"),
        description="قيمة المخزون بالتكلفة بنداً بنداً",
    ),
    ReportSpec(
        key="stock-movement",
        label="حركة المخزون",
        category="inventory",
        method="stock_movement_report",
        icon="🔄",
        params=("start", "end"),
        description="ما دخل وما خرج من المخزون",
    ),
    ReportSpec(
        key="stock-aging",
        label="أعمار المخزون",
        category="inventory",
        method="stock_aging",
        icon="🕰️",
        params=("as_of",),
        description="المخزون الراكد وبطيء الحركة",
    ),
    ReportSpec(
        key="reorder",
        label="إعادة الطلب",
        category="inventory",
        method="reorder_report",
        icon="🛒",
        description="أصناف وصلت لحد إعادة الطلب",
    ),
    ReportSpec(
        key="item-profitability",
        label="ربحية الأصناف",
        category="inventory",
        method="item_profitability",
        icon="💎",
        params=("start", "end"),
        description="الربح والهامش لكل صنف",
    ),
    ReportSpec(
        key="warehouse-stock",
        label="مخزون المخازن",
        category="inventory",
        method="warehouse_stock",
        icon="🏬",
        description="قيمة المخزون في كل مخزن",
    ),
    # --------------------------------------------------------------- sales
    ReportSpec(
        key="sales-summary",
        label="ملخص المبيعات",
        category="sales",
        method="sales_summary",
        icon="🧾",
        params=("start", "end"),
        description="صافي المبيعات والخصم والضريبة والإجمالي",
    ),
    ReportSpec(
        key="purchases-summary",
        label="ملخص المشتريات",
        category="sales",
        method="purchases_summary",
        icon="📥",
        params=("start", "end"),
        description="صافي المشتريات والضريبة والإجمالي",
    ),
    ReportSpec(
        key="sales-by-customer",
        label="المبيعات حسب العميل",
        category="sales",
        method="sales_by_customer",
        icon="📊",
        params=("start", "end"),
        description="المبيعات موزعة على العملاء",
    ),
    ReportSpec(
        key="purchases-by-supplier",
        label="المشتريات حسب المورد",
        category="sales",
        method="purchases_by_supplier",
        icon="📦",
        params=("start", "end"),
        description="المشتريات موزعة على الموردين",
    ),
    ReportSpec(
        key="profit-by-source",
        label="الربح حسب المصدر",
        category="sales",
        method="profit_by_source",
        icon="🏭",
        params=("start", "end"),
        description="ربحك من المُصنّع مقابل المُشترى، منفصلين",
    ),
    ReportSpec(
        key="discounts",
        label="الخصومات",
        category="sales",
        method="discount_report",
        icon="🔻",
        params=("start", "end"),
        description="الخصم الممنوح لكل عميل ونسبته",
    ),
    ReportSpec(
        key="monthly-trend",
        label="الاتجاه الشهري",
        category="sales",
        method="monthly_trend",
        icon="📅",
        params=("year",),
        description="الإيراد والتكلفة والربح شهراً بشهر",
    ),
    # ----------------------------------------------------------------- tax
    ReportSpec(
        key="vat",
        label="ضريبة القيمة المضافة",
        category="tax",
        method="vat_report",
        icon="🧾",
        params=("start", "end"),
        description="ضريبة المخرجات مقابل المدخلات وصافي المستحق",
    ),
    # -------------------------------------------------------------- custom
    ReportSpec(
        key="custom",
        label="تقرير مخصص (محوري)",
        category="custom",
        method="custom_report",
        icon="🧩",
        params=("rows", "columns", "values", "start", "end", "kind", "contact_id", "warehouse_id", "item_id"),
        list_params=("rows", "columns", "values"),
        description="اختر الصفوف والأعمدة والقيم التي تريدها",
    ),
)


REPORTS_BY_KEY: dict[str, ReportSpec] = {spec.key: spec for spec in REPORTS}


def by_category() -> list[dict]:
    """The catalogue grouped for the client menu, in category order."""
    grouped: list[dict] = []
    for key, label in CATEGORIES:
        items = [spec for spec in REPORTS if spec.category == key]
        if not items:
            continue
        grouped.append(
            {
                "key": key,
                "label": label,
                "reports": [
                    {
                        "key": spec.key,
                        "label": spec.label,
                        "icon": spec.icon,
                        "description": spec.description,
                        "params": list(spec.params),
                        "list_params": list(spec.list_params),
                    }
                    for spec in items
                ],
            }
        )
    return grouped
