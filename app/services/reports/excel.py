"""Turn any report into an Excel workbook.

Every report returns a nested dict of totals, lists of rows and breakdowns. The
writer walks that structure generically instead of having a bespoke sheet per
report, so a report added later is exportable the moment it exists — there is
no list of report names to keep in step.

Layout rules, chosen so the file is readable and re-usable rather than merely
valid:

* Scalars become a two-column summary block at the top.
* Each list becomes its own sheet, named after the key, with a header row.
* A list of dicts becomes a table; a list of scalars becomes one column.
* Nested dicts are flattened into columns (``sales.tax`` becomes ``sales tax``).
* Numbers are written as numbers with a currency format, so Excel can sum
  them; dates as dates. A column of text-formatted figures cannot be totalled,
  which defeats the purpose of exporting.
* Headers are frozen and autofiltered, and Arabic column widths are widened to
  fit, so the sheet is usable without any manual tidying.

RTL is set on each sheet because the app is Arabic-first; without it Excel
renders the column order backwards from the on-screen report.
"""
from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADER_FILL = PatternFill("solid", fgColor="0F766E")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=13, color="0F766E")
LABEL_FONT = Font(bold=True, size=11)
TOTAL_FILL = PatternFill("solid", fgColor="E6F4F1")
THIN = Side(style="thin", color="D5DBDB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY_FORMAT = "#,##0.00"
INT_FORMAT = "#,##0"
PERCENT_FORMAT = '0.00"%"'
DATE_FORMAT = "yyyy-mm-dd"

#: Keys whose values are money and should be summed, not averaged.
_MONEY_HINTS = (
    "amount",
    "total",
    "balance",
    "cost",
    "revenue",
    "profit",
    "value",
    "price",
    "tax",
    "debit",
    "credit",
    "spend",
    "discount",
    "paid",
    "outstanding",
    "closing",
    "opening",
    "shortfall",
    "due",
)
_PERCENT_HINTS = ("percent", "rate", "margin")
_DATE_HINTS = ("date", "received_at", "closed_at", "as_of")
_INT_HINTS = ("count", "quantity", "days", "age_days", "qty")

#: Arabic labels, so the workbook reads like the screen it came from. A key
#: missing here falls back to a spaced-out English label rather than failing,
#: which keeps a newly added report exportable before its label is written.
_LABELS = {
    "code": "الكود",
    "party": "الجهة",
    "name": "الاسم",
    "name_ar": "الاسم",
    "account": "الحساب",
    "account_id": "الحساب",
    "type": "النوع",
    "debit": "مدين",
    "credit": "دائن",
    "balance": "الرصيد",
    "amount": "المبلغ",
    "total": "الإجمالي",
    "date": "التاريخ",
    "entry_date": "التاريخ",
    "issue_date": "تاريخ الفاتورة",
    "due_date": "تاريخ الاستحقاق",
    "reference": "المرجع",
    "memo": "البيان",
    "number": "الرقم",
    "description": "الوصف",
    "quantity": "الكمية",
    "unit_price": "السعر",
    "unit_cost": "التكلفة",
    "cost": "التكلفة",
    "revenue": "الإيراد",
    "profit": "الربح",
    "margin_percent": "الهامش %",
    "share_percent": "النسبة %",
    "tax": "الضريبة",
    "net": "الصافي",
    "gross": "الإجمالي",
    "discount": "الخصم",
    "contact": "الجهة",
    "contact_id": "الجهة",
    "customer": "العميل",
    "supplier": "المورد",
    "item": "الصنف",
    "item_id": "الصنف",
    "warehouse": "المخزن",
    "status": "الحالة",
    "invoice_count": "عدد الفواتير",
    "count": "العدد",
    "outstanding": "المتبقي",
    "paid": "المدفوع",
    "opening": "الافتتاحي",
    "closing": "الختامي",
    "running_balance": "الرصيد",
    "balance_before": "الرصيد قبل",
    "age_days": "العمر (يوم)",
    "days": "الأيام",
    "current": "جاري",
    "value": "القيمة",
    "quantity_remaining": "الكمية المتبقية",
    "quantity_received": "الكمية المستلمة",
    "days_overdue": "أيام التأخير",
    "limit": "الحد الأقصى",
    "month": "الشهر",
    "year": "السنة",
    "shortfall": "النقص",
    "reorder_level": "حد الطلب",
    "on_hand": "المتاح",
    "available": "المتاح",
    "tax_total": "إجمالي الضريبة",
    "tax_rate": "نسبة الضريبة",
    "cost_of_sales": "تكلفة المبيعات",
    "cost_of_goods_sold": "تكلفة المبيعات",
    "gross_profit": "الربح الإجمالي",
    "gross_margin_percent": "هامش الربح الإجمالي %",
    "net_profit": "صافي الربح",
    "net_margin_percent": "هامش الربح الصافي %",
    "income": "الإيرادات",
    "expense": "المصروفات",
    "total_revenue": "إجمالي الإيرادات",
    "total_expenses": "إجمالي المصروفات",
    "total_cost_of_sales": "إجمالي تكلفة المبيعات",
    "total_assets": "إجمالي الأصول",
    "total_liabilities": "إجمالي الالتزامات",
    "total_equity": "حقوق الملكية",
    "total_debit": "إجمالي المدين",
    "total_credit": "إجمالي الدائن",
    "total_value": "إجمالي القيمة",
    "total_quantity": "إجمالي الكمية",
    "total_in": "إجمالي الوارد",
    "total_out": "إجمالي المنصرف",
    "total_purchases": "إجمالي المشتريات",
    "receivables": "المستحق لك",
    "payables": "المستحق عليك",
    "inventory_value": "قيمة المخزون",
    "cash": "النقدية",
    "output_tax": "ضريبة المخرجات",
    "input_tax": "ضريبة المدخلات",
    "net_payable": "صافي المستحق",
    "overdue_count": "عدد المتأخرات",
    "overdue_total": "إجمالي المتأخرات",
    "balanced": "متوازن",
    "costing_method": "طريقة التكلفة",
    "average_invoice": "متوسط الفاتورة",
    "lines": "البنود",
    "movements": "الحركات",
    "entries": "القيود",
    "contacts": "الجهات",
    "customers": "العملاء",
    "suppliers": "الموردون",
    "items": "الأصناف",
    "accounts": "الحسابات",
    "rows": "الصفوف",
    "months": "الشهور",
    "by_type": "حسب النوع",
    "asset_rows": "الأصول",
    "liability_rows": "الالتزامات",
    "equity_rows": "حقوق الملكية",
    "inflows": "التدفقات الداخلة",
    "outflows": "التدفقات الخارجة",
    "net_change": "صافي التغير",
    "discount_total": "إجمالي الخصم",
    "sold_quantity": "الكمية المبيعة",
    "periods": "الفترات",
    "status_label": "الحالة",
    "warehouse_id": "المخزن",
    "item_code": "كود الصنف",
    "unit": "الوحدة",
    "customer_name": "العميل",
    "supplier_name": "المورد",
    "item_name": "الصنف",
    "document": "المستند",
    "kind": "النوع",
    "source": "المصدر",
    "source_type": "المصدر",
    "transaction_type": "نوع الحركة",
    "direction": "الاتجاه",
    "note": "ملاحظة",
    "credit_limit": "الحد الائتماني",
    "phone": "الهاتف",
    "email": "البريد",
    "profit_margin": "هامش الربح",
    "discount_rate": "نسبة الخصم",
    "tax_percent": "نسبة الضريبة",
    "bucket": "الفئة",
    "band": "الفئة",
    "from_date": "من تاريخ",
    "to_date": "إلى تاريخ",
    "as_of": "بتاريخ",
    "upto": "حتى تاريخ",
    "start": "من تاريخ",
    "end": "إلى تاريخ",
    "quantity_in": "الوارد",
    "quantity_out": "المنصرف",
    "net_quantity": "الصافي",
    "is_low": "أقل من الحد",
    "taxable": "الوعاء الضريبي",
    "rate": "النسبة",
    "weighted_average": "المتوسط المرجح",
    "grand_total": "الإجمالي العام",
}


def _is_percent(key: str) -> bool:
    return any(hint in key for hint in _PERCENT_HINTS)


def _is_money(key: str) -> bool:
    return any(hint in key for hint in _MONEY_HINTS) and not _is_percent(key)


def _is_date(key: str) -> bool:
    return any(hint in key for hint in _DATE_HINTS)


def _is_count(key: str) -> bool:
    return any(hint in key for hint in _INT_HINTS)


def _column_format(key: str) -> str | None:
    if _is_percent(key):
        return PERCENT_FORMAT
    if _is_money(key):
        return MONEY_FORMAT
    if _is_count(key):
        return INT_FORMAT
    if _is_date(key):
        return DATE_FORMAT
    return None


def _label(key: str) -> str:
    """A dict key turned into a readable header.

    Nested keys arrive as ``"sales tax"``; each word is looked up, so a nested
    breakdown is labelled as well as a flat one.
    """
    words = str(key).split()
    if len(words) > 1:
        return " ".join(_LABELS.get(word, word.replace("_", " ").title()) for word in words)
    return _LABELS.get(key, key.replace("_", " ").strip().title())


def _flatten(row: dict, prefix: str = "") -> dict:
    """Collapse a nested dict into a flat one, joining keys with a space.

    ``{"sales": {"tax": 10}}`` becomes ``{"sales tax": 10}`` so a nested
    breakdown still fits a single table rather than being dropped.
    """
    flat: dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}{key}".strip()
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{name} "))
        elif isinstance(value, list):
            # A nested list cannot live in one cell usefully; it is exported as
            # its own sheet by the caller, so here it is summarised as a count.
            flat[f"{name} count"] = len(value)
        else:
            flat[name] = value
    return flat


def _scalar_blocks(payload: dict) -> list[tuple[str, Any]]:
    """Top-level scalars, in the order the report returned them.

    A key starting with ``_`` describes the report rather than being part of it,
    so it is skipped here and in the list loop below.
    """
    return [
        (key, value)
        for key, value in payload.items()
        if not isinstance(value, (dict, list)) and not key.startswith("_")
    ]


def _sheet_name(key: str, taken: set[str]) -> str:
    """A valid, unique Excel sheet name (31 chars, no ``[]:*?/\\``)."""
    name = re.sub(r"[\[\]:*?/\\]", " ", _label(key))[:31].strip() or "Sheet"
    candidate, counter = name, 2
    while candidate.lower() in taken:
        suffix = f" {counter}"
        candidate = f"{name[: 31 - len(suffix)]}{suffix}"
        counter += 1
    taken.add(candidate.lower())
    return candidate


def _write_scalars(sheet: Worksheet, payload: dict, title: str) -> int:
    """Write the summary block and return the row index just past it."""
    sheet.cell(row=1, column=1, value=title).font = TITLE_FONT
    scalars = _scalar_blocks(payload)
    if not scalars:
        # A report of lists only: leave the title as the sole summary row so the
        # first table can start right below it without an empty header block.
        return 3

    sheet.cell(row=2, column=1, value="البند").font = LABEL_FONT
    sheet.cell(row=2, column=2, value="القيمة").font = LABEL_FONT
    for cell in (sheet.cell(row=2, column=1), sheet.cell(row=2, column=2)):
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")

    row = 3
    for key, value in scalars:
        sheet.cell(row=row, column=1, value=_label(key))
        cell = sheet.cell(row=row, column=2, value=_excel_value(value))
        fmt = _column_format(key)
        if fmt:
            cell.number_format = fmt
        row += 1
    return row + 1


def _excel_value(value: Any) -> Any:
    """Convert a Python value into something openpyxl writes natively.

    Decimals are written as floats so Excel treats them as numbers. A Decimal
    left as-is would be written but not always recognised as numeric by other
    spreadsheet tools.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime,)):
        return value
    if isinstance(value, date):
        return value
    return value


def _write_table(sheet: Worksheet, rows: list, start_row: int) -> None:
    """Write a list of dicts (or scalars) as a table with a header row."""
    if not rows:
        sheet.cell(row=start_row, column=1, value="لا توجد بيانات")
        return

    if isinstance(rows[0], dict):
        headers: list[str] = []
        for row in rows:
            for key in _flatten(row):
                if key not in headers:
                    headers.append(key)
        for column, key in enumerate(headers, start=1):
            cell = sheet.cell(row=start_row, column=column, value=_label(key))
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = BORDER
        for offset, row in enumerate(rows, start=1):
            flat = _flatten(row)
            for column, key in enumerate(headers, start=1):
                cell = sheet.cell(row=start_row + offset, column=column, value=_excel_value(flat.get(key)))
                fmt = _column_format(key)
                if fmt:
                    cell.number_format = fmt
                cell.border = BORDER
        last_row = start_row + len(rows)
        sheet.auto_filter.ref = f"A{start_row}:{get_column_letter(len(headers))}{last_row}"
        _fit_columns(sheet, headers, rows)
    else:
        sheet.cell(row=start_row, column=1, value="القيمة").fill = HEADER_FILL
        sheet.cell(row=start_row, column=1).font = HEADER_FONT
        for offset, value in enumerate(rows, start=1):
            sheet.cell(row=start_row + offset, column=1, value=_excel_value(value))

    sheet.freeze_panes = sheet.cell(row=start_row + 1, column=1)


def _fit_columns(sheet: Worksheet, headers: list[str], rows: list[dict]) -> None:
    """Size each column to its widest cell, within a sane range.

    Arabic text is wider per character than the Latin text openpyxl measures
    for, so a minimum width is applied rather than trusting the raw length.
    """
    for index, key in enumerate(headers, start=1):
        longest = len(_label(key))
        for row in rows[:200]:
            value = _flatten(row).get(key)
            if value is None:
                continue
            longest = max(longest, len(str(value)))
        sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 4, 12), 42)


def _looks_like_rows(value: list) -> bool:
    """True when a list is tabular (dicts) or a plain column of values."""
    if not value:
        return True
    return all(not isinstance(item, list) for item in value)


def build_workbook(report_key: str, payload: dict, *, title: str | None = None) -> bytes:
    """Render one report payload into an .xlsx file, returned as bytes.

    The first sheet holds the summary scalars; every list in the payload gets
    its own sheet. A payload that is only lists (no scalars) still produces a
    readable workbook, with the first list on the summary sheet.
    """
    workbook = Workbook()
    summary = workbook.active
    summary.title = _sheet_name("الملخص", set())
    summary.sheet_view.rightToLeft = True
    next_row = _write_scalars(summary, payload, title or _label(report_key))

    taken = {summary.title.lower()}
    first_list_written = False
    for key, value in payload.items():
        if key.startswith("_") or not isinstance(value, list) or not _looks_like_rows(value):
            continue
        if not _scalar_blocks(payload) and not first_list_written:
            # Nothing but lists: keep the first on the summary sheet so the
            # file is never a lone empty page.
            _write_table(summary, value, next_row)
            first_list_written = True
            continue
        sheet = workbook.create_sheet(_sheet_name(key, taken))
        sheet.sheet_view.rightToLeft = True
        _write_table(sheet, value, 1)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def suggested_filename(report_key: str, company: str = "", suffix: str = "") -> str:
    """A safe, descriptive filename: ``report-company-2026-11-18.xlsx``."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    parts = [report_key.replace("/", "-")]
    if company:
        parts.append(re.sub(r"[^\w\u0600-\u06FF-]+", "-", company).strip("-"))
    if suffix:
        parts.append(suffix)
    parts.append(stamp)
    return "-".join(filter(None, parts)) + ".xlsx"
