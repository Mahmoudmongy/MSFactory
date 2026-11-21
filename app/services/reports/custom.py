"""The custom (pivot-style) report.

The client asked for something like an Excel pivot table. A real pivot means
running whatever the user types, which against a shared customer database is a
data-leak waiting to happen: one user could read another company's figures, or
inject SQL.

So this is a pivot over a *fixed menu*. The user picks which of the known
dimensions go down the side, which go across the top, which known measures to
total, and the usual filters. Nothing the user sends is ever interpolated into
SQL — every name is looked up in the dictionaries below and turned into a
column of the query this module writes. An unknown name is rejected, not passed
through.

Keys beginning with an underscore describe the report's shape and are not data;
the screen and the Excel writer skip them.

The numbers come from the same ``compute_line`` used to write invoices, so a
figure here agrees with the invoice it came from. Aggregation therefore happens
in Python rather than in SQL: per-line rounding is what makes the totals tie
out, and a pivot that disagrees with the ledger by a cent is worse than no pivot.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from ...core.errors import ValidationError
from ...db.models import Contact, Invoice, InvoiceLine, Item, Warehouse
from ...domain.money import compute_line
from .base import ZERO, money

#: What the user may put down the side or across the top.
DIMENSIONS: dict[str, str] = {
    "party": "الجهة",
    "item": "الصنف",
    "warehouse": "المخزن",
    "month": "الشهر",
    "year": "السنة",
    "kind": "النوع",
    "source": "المصدر",
}

#: What the user may total.
MEASURES: dict[str, str] = {
    "net": "صافي",
    "cost": "التكلفة",
    "profit": "الربح",
    "quantity": "الكمية",
    "tax": "الضريبة",
    "discount": "الخصم",
    "total": "الإجمالي",
}

#: Filters the pivot accepts, each mapped to the column it constrains.
FILTERS: dict[str, str] = {
    "start": "من تاريخ",
    "end": "إلى تاريخ",
    "kind": "نوع الفاتورة",
    "contact_id": "الجهة",
    "warehouse_id": "المخزن",
    "item_id": "الصنف",
}

NO_PARTY = "بدون جهة"
NO_ITEM = "بدون صنف"
NO_WAREHOUSE = "بدون مخزن"
MANUFACTURED = "مصنّع"
PURCHASED = "مُشترى"
UNCLASSIFIED = "غير محدد"
GRAND_TOTAL = "الإجمالي"

KIND_LABELS = {"sales": "مبيعات", "purchase": "مشتريات"}

#: A pivot is meant to be read, not scrolled forever. Past this many output
#: cells the request is refused with a clear message rather than returning a
#: payload no screen can render.
MAX_CELLS = 20000


class CustomReportMixin:
    """A whitelisted pivot over invoice lines."""

    def custom_report(
        self,
        rows: list[str] | None = None,
        columns: list[str] | None = None,
        values: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        kind: str | None = None,
        contact_id: int | None = None,
        warehouse_id: int | None = None,
        item_id: int | None = None,
    ) -> dict:
        """Total the chosen measures, grouped by the chosen dimensions."""
        row_dims = self._check(rows or ["month"], DIMENSIONS, "صف")
        col_dims = self._check(columns or [], DIMENSIONS, "عمود")
        measures = self._check(values or ["net"], MEASURES, "قيمة")
        if kind not in (None, "", "sales", "purchase"):
            raise ValidationError("نوع الفاتورة غير معروف")

        # Rows are the grouping key; the value columns are the measure crossed
        # with the column dimension (or the bare measure when there is none).
        cells: dict[tuple, dict[str, Decimal]] = {}
        totals: dict[str, Decimal] = dict.fromkeys(measures, ZERO)
        column_keys: list[str] = []

        for line in self._pivot_lines(start, end, kind, contact_id, warehouse_id, item_id):
            amounts = compute_line(line.quantity, line.unit_price, line.discount_rate, line.tax_rate)
            figures = {
                "net": amounts.net,
                "cost": money(line.cost_amount),
                "profit": amounts.net - money(line.cost_amount),
                "quantity": Decimal(str(line.quantity or 0)),
                "tax": amounts.tax,
                "discount": amounts.discount,
                "total": amounts.total,
            }
            key = tuple(self._dimension_value(name, line) for name in row_dims)
            column_label = " · ".join(self._dimension_value(name, line) for name in col_dims)
            bucket = cells.setdefault(key, {})
            for measure in measures:
                # The measure word stays in the key so the screen and the
                # workbook both recognise it as money and format it as such.
                cell = f"{measure} {column_label}".strip() if col_dims else measure
                if cell not in column_keys:
                    column_keys.append(cell)
                bucket[cell] = bucket.get(cell, ZERO) + figures[measure]
                totals[cell] = totals.get(cell, ZERO) + figures[measure]

        if (len(cells) + 1) * (len(column_keys) + 1) > MAX_CELLS:
            raise ValidationError("التقرير كبير جداً، ضيّق الفلاتر أو اختر أبعاداً أقل")

        output: list[dict] = []
        for key in sorted(cells, key=lambda k: tuple(str(part) for part in k)):
            row: dict = dict(zip(row_dims, key, strict=True))
            for cell in column_keys:
                row[cell] = money(cells[key].get(cell, ZERO))
            output.append(row)

        if output:
            total_row: dict = dict.fromkeys(row_dims, GRAND_TOTAL)
            for cell in column_keys:
                total_row[cell] = money(totals.get(cell, ZERO))
            output.append(total_row)

        # The underscore marks a key as describing the report rather than being
        # part of it, so neither the screen nor the workbook tries to render it
        # as a table. Clients (and tests) still read them.
        return {
            "rows": output,
            "_row_dimensions": [{"key": name, "label": DIMENSIONS[name]} for name in row_dims],
            "_column_dimensions": [{"key": name, "label": DIMENSIONS[name]} for name in col_dims],
            "_measures": [{"key": name, "label": MEASURES[name]} for name in measures],
            "_count": len(output) - (1 if output else 0),
        }

    # ---------------------------------------------------------------- helpers

    def _check(self, names: list[str], allowed: dict[str, str], what: str) -> list[str]:
        """Validate user-supplied names against the whitelist.

        This is the security boundary: only a name in ``allowed`` survives, and
        it is used solely as a dictionary key, never in a query string.
        """
        checked: list[str] = []
        for name in names:
            if name not in allowed:
                raise ValidationError(f"اسم {what} غير معروف: {name}")
            if name not in checked:
                checked.append(name)
        return checked

    def _pivot_lines(
        self,
        start: date | None,
        end: date | None,
        kind: str | None,
        contact_id: int | None,
        warehouse_id: int | None,
        item_id: int | None,
    ):
        """Invoice lines with their party, item and warehouse already joined.

        Only the columns the pivot reads are selected, so the row stream stays
        narrow; the join is done in SQL so no per-row round trip happens.
        """
        stmt = (
            select(
                Invoice.kind,
                Invoice.issue_date,
                InvoiceLine.quantity,
                InvoiceLine.unit_price,
                InvoiceLine.discount_rate,
                InvoiceLine.tax_rate,
                InvoiceLine.cost_amount,
                InvoiceLine.cost_manufactured,
                InvoiceLine.cost_purchased,
                Contact.name,
                Item.name,
                Warehouse.name,
            )
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .join(Contact, Contact.id == Invoice.contact_id)
            .outerjoin(Item, Item.id == InvoiceLine.item_id)
            .outerjoin(Warehouse, Warehouse.id == InvoiceLine.warehouse_id)
            .where(
                InvoiceLine.company_id == self.company_id,
                Invoice.status != "void",
            )
        )
        if start:
            stmt = stmt.where(Invoice.issue_date >= start)
        if end:
            stmt = stmt.where(Invoice.issue_date <= end)
        if kind:
            stmt = stmt.where(Invoice.kind == kind)
        if contact_id:
            stmt = stmt.where(Invoice.contact_id == contact_id)
        if warehouse_id:
            stmt = stmt.where(InvoiceLine.warehouse_id == warehouse_id)
        if item_id:
            stmt = stmt.where(InvoiceLine.item_id == item_id)
        return self.db.execute(stmt).all()

    def _dimension_value(self, name: str, line) -> str:
        """Turn a whitelisted dimension name into this row's value for it."""
        if name == "party":
            return line[9] or NO_PARTY
        if name == "item":
            return line[10] or NO_ITEM
        if name == "warehouse":
            return line[11] or NO_WAREHOUSE
        if name == "month":
            return f"{line[1]:%Y-%m}"
        if name == "year":
            return str(line[1].year)
        if name == "kind":
            return KIND_LABELS.get(line[0], line[0])
        if name == "source":
            made = money(line[7])
            bought = money(line[8])
            if made == ZERO and bought == ZERO:
                return UNCLASSIFIED
            return MANUFACTURED if made >= bought else PURCHASED
        return ""
