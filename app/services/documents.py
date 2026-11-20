"""Printable documents: a server-rendered A4 page the browser turns into PDF.

Why HTML and not a PDF library: the browser's own "Save as PDF" already lays
out Arabic, RTL text and fonts correctly, needs no dependency, and produces a
text-searchable file. A PDF library would have to embed an Arabic font and
handle bidi shaping by hand, which is a large amount of work to match what the
browser does for free. The legacy UI instead screenshots the page with
html2canvas, which yields an image, requires a CDN, and cannot be searched.

Everything here is escaped before it reaches the page. Company names, contact
names and line descriptions are all user input, and this document is served
from the same origin as the API, so an unescaped value would be stored XSS.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

from ..db.models import Company, Contact, Invoice, InvoiceLine
from ..repositories.core import SettingRepository

# Arabic labels for the kinds the invoice model can hold. A kind that is not
# here still prints; the raw value is shown rather than a blank cell.
KIND_LABELS = {
    "sales": "فاتورة بيع",
    "purchase": "فاتورة شراء",
    "sales_return": "مرتجع بيع",
    "purchase_return": "مرتجع شراء",
}

STATUS_LABELS = {
    "draft": "مسودة",
    "issued": "صادرة",
    "partially_paid": "مدفوعة جزئياً",
    "paid": "مدفوعة",
    "void": "ملغاة",
}


def _text(value) -> str:
    """Escape a value for HTML, turning None into an empty string."""
    return escape("" if value is None else str(value))


def _amount(value: Decimal | int | float | None, currency: str = "") -> str:
    """Format money with thousands separators and no spurious decimals.

    Arabic-Indic digits are deliberately avoided: invoices are read by
    accountants and exported to tools that expect ASCII numerals, and mixing
    the two in one document is worse than picking one.
    """
    try:
        number = Decimal(str(value or 0))
    except Exception:
        number = Decimal(0)
    text = f"{number:,.2f}"
    return f"{text} {currency}".strip()


def _quantity(value: Decimal | None) -> str:
    """Show 5 rather than 5.0000, but keep 2.5 as 2.5."""
    try:
        number = Decimal(str(value or 0))
    except Exception:
        return "0"
    normalised = number.normalize()
    if normalised == normalised.to_integral():
        return str(normalised.quantize(Decimal(1)))
    return format(normalised, "f")


def _date(value: date | None) -> str:
    return value.isoformat() if value else "—"


def line_amounts(line: InvoiceLine) -> tuple[Decimal, Decimal, Decimal]:
    """Gross, discount and tax for one line, as the invoice screen computes them.

    Kept in one place so the printed document and the screen cannot drift: both
    apply the discount to the gross and then the tax to the discounted figure.
    """
    gross = Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_price or 0))
    discount = gross * Decimal(str(line.discount_rate or 0)) / 100
    net = gross - discount
    tax = net * Decimal(str(line.tax_rate or 0)) / 100
    return gross, discount, tax


def _print_styles() -> str:
    """Styles for A4, with a screen preview that looks like the printed page.

    ``@page`` sets the sheet; the ``print`` block drops the toolbar and the
    page shadow so the browser's own header and footer are the only chrome.
    """
    return """
  @page { size: A4; margin: 14mm 12mm; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #eef1f5; color: #111827;
    font-family: "Segoe UI", "Cairo", "Tahoma", Arial, sans-serif;
    direction: rtl; text-align: right; -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .sheet {
    width: 210mm; min-height: 297mm; margin: 16px auto; padding: 14mm 12mm;
    background: #fff; box-shadow: 0 2px 16px rgba(15, 23, 42, .16);
  }
  .toolbar {
    display: flex; gap: 8px; justify-content: center; padding: 12px 8px 0;
  }
  .toolbar button {
    font: inherit; font-size: 15px; min-height: 44px; padding: 0 20px;
    border-radius: 10px; border: 1px solid #cbd5e1; background: #fff;
    color: #0f172a; cursor: pointer;
  }
  .toolbar button.primary { background: #0f766e; border-color: #0f766e; color: #fff; }
  .head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
  .brand { display: flex; gap: 12px; align-items: center; min-width: 0; }
  .brand img { max-height: 64px; max-width: 160px; object-fit: contain; }
  .brand h1 { margin: 0; font-size: 20px; }
  .brand .meta { margin: 2px 0 0; font-size: 12px; color: #4b5563; line-height: 1.6; }
  .doc { text-align: left; flex: 0 0 auto; }
  .doc h2 { margin: 0; font-size: 22px; letter-spacing: .2px; }
  .doc .number { font-size: 15px; color: #0f766e; font-weight: 700; margin-top: 2px; }
  .doc .badge {
    display: inline-block; margin-top: 6px; padding: 3px 10px; border-radius: 999px;
    font-size: 12px; background: #e6f4f1; color: #0f766e; border: 1px solid #bfe3dc;
  }
  .parties { display: flex; gap: 16px; margin-top: 18px; }
  .party { flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; }
  .party h3 { margin: 0 0 6px; font-size: 12px; color: #6b7280; font-weight: 600; }
  .party .name { font-size: 15px; font-weight: 700; }
  .party .line { font-size: 12px; color: #4b5563; margin-top: 3px; }
  table { width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 13px; }
  thead th {
    background: #f3f4f6; font-size: 12px; color: #374151; text-align: right;
    padding: 9px 8px; border-bottom: 1px solid #d1d5db; white-space: nowrap;
  }
  tbody td { padding: 8px; border-bottom: 1px solid #eef0f3; vertical-align: top; }
  tbody tr:last-child td { border-bottom: 1px solid #d1d5db; }
  .num { text-align: left; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .totals { margin-top: 14px; margin-inline-start: auto; width: 62mm; font-size: 13px; }
  .totals .row { display: flex; justify-content: space-between; padding: 5px 0; }
  .totals .row.grand {
    border-top: 2px solid #111827; margin-top: 4px; padding-top: 7px;
    font-size: 15px; font-weight: 700;
  }
  .notes { margin-top: 18px; font-size: 12px; color: #374151; }
  .notes h3 { margin: 0 0 4px; font-size: 12px; color: #6b7280; }
  .footer {
    margin-top: 24px; padding-top: 10px; border-top: 1px solid #e5e7eb;
    font-size: 11px; color: #6b7280; text-align: center; white-space: pre-line;
  }
  .void {
    margin-top: 14px; padding: 8px 12px; border-radius: 8px; font-size: 13px;
    background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; font-weight: 700;
  }
  @media print {
    body { background: #fff; }
    .toolbar { display: none; }
    .sheet { width: auto; min-height: 0; margin: 0; padding: 0; box-shadow: none; }
    thead { display: table-header-group; }
    tr { break-inside: avoid; }
  }
"""


def _party_block(title: str, name: str, lines: list[str]) -> str:
    body = "".join(f'<div class="line">{_text(line)}</div>' for line in lines if line)
    return f"""<div class="party">
        <h3>{_text(title)}</h3>
        <div class="name">{_text(name)}</div>
        {body}
      </div>"""


def render_invoice(
    invoice: Invoice,
    contact: Contact | None,
    company: Company,
    settings: dict[str, str],
) -> str:
    """Build the printable page for one invoice.

    The caller has already resolved tenant ownership; this function only
    formats. It never reads the database, so it cannot leak another company's
    rows.
    """
    company_name = settings.get("company_name") or company.name
    currency = invoice.currency or company.base_currency or ""
    kind_label = KIND_LABELS.get(invoice.kind, invoice.kind)
    status_label = STATUS_LABELS.get(invoice.status, invoice.status)

    brand_meta = []
    if settings.get("address"):
        brand_meta.append(settings["address"])
    if settings.get("phone"):
        brand_meta.append(settings["phone"])
    tax_number = settings.get("tax_number") or company.tax_number
    if tax_number:
        brand_meta.append(f"الرقم الضريبي: {tax_number}")

    logo = settings.get("logo", "")
    logo_html = f'<img src="{_text(logo)}" alt="" />' if logo.startswith("data:image/") else ""

    contact_lines = []
    if contact is not None:
        if contact.address:
            contact_lines.append(contact.address)
        if contact.phone:
            contact_lines.append(f"هاتف: {contact.phone}")
        if contact.email:
            contact_lines.append(contact.email)
        if contact.tax_number:
            contact_lines.append(f"الرقم الضريبي: {contact.tax_number}")

    rows = []
    for index, line in enumerate(invoice.lines, start=1):
        gross, discount, tax = line_amounts(line)
        net = gross - discount + tax
        rows.append(
            f"""<tr>
          <td class="num">{index}</td>
          <td>{_text(line.description or "—")}</td>
          <td class="num">{_quantity(line.quantity)}</td>
          <td class="num">{_amount(line.unit_price)}</td>
          <td class="num">{_amount(discount) if discount else "—"}</td>
          <td class="num">{_amount(tax) if tax else "—"}</td>
          <td class="num">{_amount(net)}</td>
        </tr>"""
        )
    if not rows:
        rows.append('<tr><td colspan="7" style="text-align:center;color:#6b7280">لا توجد بنود</td></tr>')

    outstanding = Decimal(str(invoice.total or 0)) - Decimal(str(invoice.amount_paid or 0))
    totals_rows = [
        f'<div class="row"><span>المجموع</span><span class="num">{_amount(invoice.subtotal, currency)}</span></div>',
    ]
    if Decimal(str(invoice.discount_total or 0)):
        totals_rows.append(
            f'<div class="row"><span>الخصم</span><span class="num">{_amount(invoice.discount_total, currency)}</span></div>'
        )
    if Decimal(str(invoice.tax_total or 0)):
        totals_rows.append(
            f'<div class="row"><span>الضريبة</span><span class="num">{_amount(invoice.tax_total, currency)}</span></div>'
        )
    totals_rows.append(
        f'<div class="row grand"><span>الإجمالي</span><span class="num">{_amount(invoice.total, currency)}</span></div>'
    )
    if Decimal(str(invoice.amount_paid or 0)):
        totals_rows.append(
            f'<div class="row"><span>المدفوع</span><span class="num">{_amount(invoice.amount_paid, currency)}</span></div>'
        )
        totals_rows.append(
            f'<div class="row"><span>المتبقي</span><span class="num">{_amount(outstanding, currency)}</span></div>'
        )

    due_html = (
        f'<div class="line">تاريخ الاستحقاق: {_date(invoice.due_date)}</div>' if invoice.due_date else ""
    )
    void_html = '<div class="void">هذه الفاتورة ملغاة — لا تُعدّ سنداً مالياً</div>' if invoice.status == "void" else ""
    notes_html = (
        f'<div class="notes"><h3>ملاحظات</h3><div>{_text(invoice.notes)}</div></div>'
        if invoice.notes
        else ""
    )
    footer = settings.get("invoice_footer", "")

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_text(kind_label)} {_text(invoice.number)}</title>
<style>{_print_styles()}</style>
</head>
<body>
  <div class="toolbar">
    <button type="button" class="primary" onclick="window.print()">🖨️ طباعة / حفظ PDF</button>
    <button type="button" onclick="window.close()">إغلاق</button>
  </div>
  <div class="sheet">
    <div class="head">
      <div class="brand">
        {logo_html}
        <div>
          <h1>{_text(company_name)}</h1>
          <p class="meta">{" · ".join(_text(part) for part in brand_meta)}</p>
        </div>
      </div>
      <div class="doc">
        <h2>{_text(kind_label)}</h2>
        <div class="number">{_text(invoice.number)}</div>
        <div class="badge">{_text(status_label)}</div>
      </div>
    </div>

    {void_html}

    <div class="parties">
      {_party_block("إلى" if invoice.kind == "sales" else "من", contact.name if contact else "—", contact_lines)}
      <div class="party">
        <h3>بيانات المستند</h3>
        <div class="line">تاريخ الإصدار: {_date(invoice.issue_date)}</div>
        {due_html}
        <div class="line">العملة: {_text(currency)}</div>
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th class="num">#</th>
          <th>الوصف</th>
          <th class="num">الكمية</th>
          <th class="num">السعر</th>
          <th class="num">الخصم</th>
          <th class="num">الضريبة</th>
          <th class="num">الإجمالي</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>

    <div class="totals">
      {"".join(totals_rows)}
    </div>

    {notes_html}
    {f'<div class="footer">{_text(footer)}</div>' if footer else ""}
  </div>
</body>
</html>"""


def invoice_title(invoice: Invoice) -> str:
    """A filename-safe description used for the browser's suggested name."""
    kind = KIND_LABELS.get(invoice.kind, invoice.kind)
    safe_number = "".join(ch for ch in invoice.number if ch.isalnum() or ch in "-_")
    return f"{kind}-{safe_number}"
