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

from ..db.models import Company, Contact, Invoice, InvoiceLine, Payment

# Arabic labels for the kinds the invoice model can hold. A kind that is not
# here still prints; the raw value is shown rather than a blank cell.
KIND_LABELS = {
    "sales": "فاتورة بيع",
    "purchase": "فاتورة شراء",
    "sales_return": "مرتجع بيع",
    "purchase_return": "مرتجع شراء",
}

PAYMENT_KIND_LABELS = {
    "receive": "سند قبض",
    "spend": "سند دفع",
}

# Egyptian vouchers print the amount in words next to the figure, because that
# is what makes a receipt checkable by eye and impossible to alter with an extra
# zero. These tables follow the standard Arabic counting rules, where 11-19 take
# a single form and 21 is "one and twenty".
_ARABIC_ONES = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS = [
    "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر",
    "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر",
]
_ARABIC_TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = [
    "", "مئة", "مئتان", "ثلاثمئة", "أربعمئة",
    "خمسمئة", "ستمئة", "سبعمئة", "ثمانمئة", "تسعمئة",
]
# (value, singular, dual, plural) - Arabic counts 1, 2 and 3-10 differently.
_ARABIC_SCALES = [
    (1_000_000_000, "مليار", "مليارا", "مليارات"),
    (1_000_000, "مليون", "مليونا", "ملايين"),
    (1_000, "ألف", "ألفا", "آلاف"),
]
# The currency word changes with the number before it: «جنيه واحد», «جنيهان»,
# «ثلاثة جنيهات», «أحد عشر جنيهاً», «مئة جنيه». Picking the wrong form is what
# makes a printed voucher look machine-translated to an Egyptian accountant.
_CURRENCY_FORMS = {
    "EGP": {
        "one": "جنيه مصري واحد",
        "two": "جنيهان مصريان",
        "few": "جنيهات مصرية",
        "many": "جنيهاً مصرياً",
        "plain": "جنيه مصري",
    },
    "USD": {
        "one": "دولار أمريكي واحد",
        "two": "دولاران أمريكيان",
        "few": "دولارات أمريكية",
        "many": "دولاراً أمريكياً",
        "plain": "دولار أمريكي",
    },
    "EUR": {
        "one": "يورو واحد",
        "two": "يوروان",
        "few": "يوروات",
        "many": "يورو",
        "plain": "يورو",
    },
    "SAR": {
        "one": "ريال سعودي واحد",
        "two": "ريالان سعوديان",
        "few": "ريالات سعودية",
        "many": "ريالاً سعودياً",
        "plain": "ريال سعودي",
    },
    "AED": {
        "one": "درهم إماراتي واحد",
        "two": "درهمان إماراتيان",
        "few": "دراهم إماراتية",
        "many": "درهماً إماراتياً",
        "plain": "درهم إماراتي",
    },
}
_MINOR_FORMS = {
    "قرش": {"one": "قرش واحد", "two": "قرشان", "few": "قروش", "many": "قرشاً", "plain": "قرش"},
    "سنت": {"one": "سنت واحد", "two": "سنتان", "few": "سنتات", "many": "سنتاً", "plain": "سنت"},
    "هللة": {"one": "هللة واحدة", "two": "هللتان", "few": "هللات", "many": "هللة", "plain": "هللة"},
    "فلس": {"one": "فلس واحد", "two": "فلسان", "few": "فلوس", "many": "فلساً", "plain": "فلس"},
}


def _three_digits(number: int) -> str:
    """1-999 in Arabic words, e.g. 342 -> «ثلاثمئة واثنان وأربعون»."""
    hundreds, remainder = divmod(number, 100)
    parts = []
    if hundreds:
        parts.append(_ARABIC_HUNDREDS[hundreds])
    if remainder:
        if remainder < 10:
            parts.append(_ARABIC_ONES[remainder])
        elif remainder < 20:
            parts.append(_ARABIC_TEENS[remainder - 10])
        else:
            tens, ones = divmod(remainder, 10)
            if ones:
                parts.append(_ARABIC_ONES[ones])
            parts.append(_ARABIC_TENS[tens])
    return " و".join(parts)


def _form_for(number: int) -> str:
    """Which currency form the final component of ``number`` requires."""
    if number == 1:
        return "one"
    if number == 2:
        return "two"
    if 3 <= number <= 10:
        return "few"
    if 11 <= number <= 99:
        return "many"
    return "plain"


def _integer_in_words(number: int) -> tuple[str, str]:
    """A whole number in Arabic words, plus the currency form it needs.

    The second value follows the *last* component: 15000 ends in a scale word,
    so the currency stays singular («خمسة عشر ألف جنيه»), while 15 does not and
    takes the accusative («خمسة عشر جنيهاً»).
    """
    if number == 0:
        return "صفر", "plain"
    parts = []
    remainder = number
    last = "plain"
    for value, singular, dual, plural in _ARABIC_SCALES:
        count, remainder = divmod(remainder, value)
        if not count:
            continue
        if count == 1:
            parts.append(singular)
        elif count == 2:
            # Construct state: «ألفا جنيه», not «ألفان جنيه».
            parts.append(dual)
        elif count <= 10:
            parts.append(f"{_three_digits(count)} {plural}")
        else:
            parts.append(f"{_three_digits(count)} {singular}")
        last = "plain"
    if remainder:
        parts.append(_three_digits(remainder))
        last = _form_for(remainder)
    return " و".join(parts), last


def _amount_in_words(value: Decimal, currency: str = "EGP") -> str:
    """Spell a money amount the way a printed voucher reads it.

    Rounds to two decimals first: a voucher states whole piastres, and a
    half-piastre would otherwise produce a phrase nobody can reconcile.
    """
    major_forms = _CURRENCY_FORMS.get(currency)
    if major_forms is None:
        name = currency or "عملة"
        major_forms = {
            "one": f"{name} واحد",
            "two": f"{name}ان",
            "few": name,
            "many": name,
            "plain": name,
        }
    minor_name = {"EGP": "قرش"}.get(currency, "سنت")
    minor_forms = _MINOR_FORMS.get(minor_name, {
        "one": f"{minor_name} واحد", "two": f"{minor_name}ان",
        "few": minor_name, "many": minor_name, "plain": minor_name,
    })

    quantised = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    negative = quantised < 0
    quantised = abs(quantised)
    major = int(quantised)
    minor = int((quantised - major) * 100)

    if major == 1:
        phrase = major_forms["one"]
    elif major == 2:
        phrase = major_forms["two"]
    elif major:
        words, form = _integer_in_words(major)
        phrase = f"{words} {major_forms[form]}"
    else:
        phrase = ""

    if minor:
        if minor == 1:
            minor_phrase = minor_forms["one"]
        elif minor == 2:
            minor_phrase = minor_forms["two"]
        else:
            minor_words, minor_form = _integer_in_words(minor)
            minor_phrase = f"{minor_words} {minor_forms[minor_form]}"
        phrase = f"{phrase} و{minor_phrase}" if phrase else minor_phrase
    if not phrase:
        phrase = f"صفر {major_forms['plain']}"
    if negative:
        phrase = f"سالب {phrase}"
    return f"فقط {phrase} لا غير"


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


def _row(label: str, value, currency: str, *, css: str = "") -> str:
    """One label/value line in the totals block."""
    return f'<div class="row{css}"><span>{label}</span><span class="num">{_amount(value, currency)}</span></div>'


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
  .voucher-amount {
    margin-top: 18px; padding: 12px 16px; border-radius: 10px;
    border: 2px solid #0f766e; background: #f0fdfa;
  }
  .voucher-amount .label { font-size: 12px; color: #0f766e; font-weight: 700; }
  .voucher-amount .value { font-size: 26px; font-weight: 800; margin-top: 2px; }
  .voucher-amount .words {
    margin-top: 6px; font-size: 13px; color: #134e4a;
    border-top: 1px dashed #99f6e4; padding-top: 6px;
  }
  .signatures {
    display: flex; gap: 16px; margin-top: 40px; justify-content: space-between;
  }
  .sig {
    flex: 1; border-top: 1px solid #9ca3af; padding-top: 6px;
    font-size: 12px; color: #4b5563; text-align: center;
  }
  @media print {
    body { background: #fff; }
    .toolbar { display: none; }
    .sheet { width: auto; min-height: 0; margin: 0; padding: 0; box-shadow: none; }
    thead { display: table-header-group; }
    tr { break-inside: avoid; }
  }
"""


def _page(title: str, inner: str) -> str:
    """The A4 shell every printable document shares."""
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_text(title)}</title>
<style>{_print_styles()}</style>
</head>
<body>
  <div class="toolbar">
    <button type="button" class="primary" onclick="window.print()">🖨️ طباعة / حفظ PDF</button>
    <button type="button" onclick="window.close()">إغلاق</button>
  </div>
  <div class="sheet">
{inner}
  </div>
</body>
</html>"""


def _brand_block(company_name: str, meta: list[str], logo_html: str) -> str:
    return f"""<div class="brand">
        {logo_html}
        <div>
          <h1>{_text(company_name)}</h1>
          <p class="meta">{" · ".join(_text(part) for part in meta)}</p>
        </div>
      </div>"""


def _doc_block(label: str, number: str, badge: str = "") -> str:
    badge_html = f'<div class="badge">{_text(badge)}</div>' if badge else ""
    return f"""<div class="doc">
        <h2>{_text(label)}</h2>
        <div class="number">{_text(number)}</div>
        {badge_html}
      </div>"""


def _company_meta(company: Company, settings: dict[str, str]) -> tuple[str, list[str], str]:
    """Company name, the small print under it, and the logo markup."""
    name = settings.get("company_name") or company.name
    meta = []
    if settings.get("address"):
        meta.append(settings["address"])
    if settings.get("phone"):
        meta.append(settings["phone"])
    tax_number = settings.get("tax_number") or company.tax_number
    if tax_number:
        meta.append(f"الرقم الضريبي: {tax_number}")
    logo = settings.get("logo", "")
    logo_html = f'<img src="{_text(logo)}" alt="" />' if logo.startswith("data:image/") else ""
    return name, meta, logo_html


def _contact_details(contact: Contact | None) -> list[str]:
    if contact is None:
        return []
    lines = []
    if contact.address:
        lines.append(contact.address)
    if contact.phone:
        lines.append(f"هاتف: {contact.phone}")
    if contact.email:
        lines.append(contact.email)
    if contact.tax_number:
        lines.append(f"الرقم الضريبي: {contact.tax_number}")
    return lines


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
    company_name, brand_meta, logo_html = _company_meta(company, settings)
    contact_lines = _contact_details(contact)
    currency = invoice.currency or company.base_currency or ""
    kind_label = KIND_LABELS.get(invoice.kind, invoice.kind)
    status_label = STATUS_LABELS.get(invoice.status, invoice.status)

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
    totals_rows = [_row("المجموع", invoice.subtotal, currency)]
    if Decimal(str(invoice.discount_total or 0)):
        totals_rows.append(_row("الخصم", invoice.discount_total, currency))
    if Decimal(str(invoice.tax_total or 0)):
        totals_rows.append(_row("الضريبة", invoice.tax_total, currency))
    totals_rows.append(_row("الإجمالي", invoice.total, currency, css=" grand"))
    if Decimal(str(invoice.amount_paid or 0)):
        totals_rows.append(_row("المدفوع", invoice.amount_paid, currency))
        totals_rows.append(_row("المتبقي", outstanding, currency))

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

    return _page(
        f"{kind_label} {invoice.number}",
        f"""    <div class="head">
      {_brand_block(company_name, brand_meta, logo_html)}
      {_doc_block(kind_label, invoice.number, status_label)}
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
    {f'<div class="footer">{_text(footer)}</div>' if footer else ""}""",
    )


def invoice_title(invoice: Invoice) -> str:
    """A filename-safe description used for the browser's suggested name."""
    kind = KIND_LABELS.get(invoice.kind, invoice.kind)
    safe_number = "".join(ch for ch in invoice.number if ch.isalnum() or ch in "-_")
    return f"{kind}-{safe_number}"


def render_payment(
    payment: Payment,
    contact: Contact | None,
    company: Company,
    settings: dict[str, str],
    bank_account_name: str = "",
    invoices: dict[int, tuple[str, date | None]] | None = None,
) -> str:
    """Build the printable page for one receipt or payment voucher.

    A voucher is mostly one number, so the page leads with the amount in words
    next to it: that is what makes a printed receipt checkable at a glance and
    is how Egyptian vouchers have always been printed.

    ``invoices`` maps invoice id to (number, issue date) for the allocations.
    It is passed in rather than read here, for the same reason as
    :func:`render_invoice`: this function must not be able to reach another
    tenant's rows.
    """
    company_name, brand_meta, logo_html = _company_meta(company, settings)
    contact_lines = _contact_details(contact)
    currency = company.base_currency or "EGP"
    kind_label = PAYMENT_KIND_LABELS.get(payment.kind, payment.kind)
    amount = Decimal(str(payment.amount or 0))

    allocations = list(payment.allocations or [])
    settled_rows = []
    for index, allocation in enumerate(allocations, start=1):
        reference = (invoices or {}).get(allocation.invoice_id, ("—", None))
        settled_rows.append(
            f"""<tr>
          <td class="num">{index}</td>
          <td>{_text(reference[0])}</td>
          <td class="num">{_date(reference[1])}</td>
          <td class="num">{_amount(allocation.amount, currency)}</td>
        </tr>"""
        )
    if not settled_rows:
        settled_rows.append(
            '<tr><td colspan="4" style="text-align:center;color:#6b7280">'
            "غير مخصصة لفواتير — دفعة على الحساب</td></tr>"
        )

    allocated = sum((Decimal(str(a.amount or 0)) for a in allocations), Decimal("0.00"))
    on_account = amount - allocated

    totals_rows = [
        f'<div class="row grand"><span>المبلغ</span>'
        f'<span class="num">{_amount(amount, currency)}</span></div>',
    ]
    if allocated:
        totals_rows.append(
            f'<div class="row"><span>مخصص لفواتير</span>'
            f'<span class="num">{_amount(allocated, currency)}</span></div>'
        )
    if on_account:
        totals_rows.append(
            f'<div class="row"><span>على الحساب</span>'
            f'<span class="num">{_amount(on_account, currency)}</span></div>'
        )

    method_html = (
        f'<div class="line">الحساب: {_text(bank_account_name)}</div>' if bank_account_name else ""
    )
    reference_html = (
        f'<div class="line">المرجع: {_text(payment.reference)}</div>' if payment.reference else ""
    )
    footer = settings.get("invoice_footer", "")

    return _page(
        f"{kind_label} {payment.number}",
        f"""    <div class="head">
      {_brand_block(company_name, brand_meta, logo_html)}
      {_doc_block(kind_label, payment.number)}
    </div>

    <div class="parties">
      {_party_block("من" if payment.kind == "receive" else "إلى", contact.name if contact else "—", contact_lines)}
      <div class="party">
        <h3>بيانات السند</h3>
        <div class="line">التاريخ: {_date(payment.date)}</div>
        {method_html}
        {reference_html}
        <div class="line">العملة: {_text(currency)}</div>
      </div>
    </div>

    <div class="voucher-amount">
      <div class="label">المبلغ</div>
      <div class="value num">{_amount(amount, currency)}</div>
      <div class="words">{_text(_amount_in_words(amount, currency))}</div>
    </div>

    <table>
      <thead>
        <tr>
          <th class="num">#</th>
          <th>الفاتورة</th>
          <th class="num">تاريخها</th>
          <th class="num">المبلغ المخصص</th>
        </tr>
      </thead>
      <tbody>
        {"".join(settled_rows)}
      </tbody>
    </table>

    <div class="totals">
      {"".join(totals_rows)}
    </div>

    <div class="signatures">
      <div class="sig"><span>المستلم</span></div>
      <div class="sig"><span>المحاسب</span></div>
      <div class="sig"><span>المدير</span></div>
    </div>

    {f'<div class="footer">{_text(footer)}</div>' if footer else ""}""",
    )


def payment_title(payment: Payment) -> str:
    """A filename-safe description used for the browser's suggested name."""
    kind = PAYMENT_KIND_LABELS.get(payment.kind, payment.kind)
    safe_number = "".join(ch for ch in payment.number if ch.isalnum() or ch in "-_")
    return f"{kind}-{safe_number}"
