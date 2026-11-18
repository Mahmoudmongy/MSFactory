"""Sales, purchasing and tax reports.

These answer the commercial questions: what sold, to whom, at what margin, and
what tax is due. Profit by origin lives here too, because "did I make more on
what I produced or on what I resold" is a sales question with an inventory
answer.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from ...db.models import Contact, Invoice, InvoiceLine
from ...domain.money import compute_line
from .base import ZERO, money, pct


class SalesMixin:
    """Sales summaries, purchases, tax and profit by origin."""

    def _sales_lines(self, start: date | None, end: date | None, kind: str = "sales"):
        """Scoped invoice lines for one document kind, excluding voided ones."""
        stmt = (
            select(InvoiceLine)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(
                InvoiceLine.company_id == self.company_id,
                Invoice.kind == kind,
                Invoice.status != "void",
            )
        )
        if start:
            stmt = stmt.where(Invoice.issue_date >= start)
        if end:
            stmt = stmt.where(Invoice.issue_date <= end)
        return stmt

    def sales_summary(self, start: date | None = None, end: date | None = None) -> dict:
        """Totals for sales: net, discount, tax, gross, and the invoice count.

        Discount and tax are reported separately rather than folded into the
        total, because a discount is a pricing decision and tax is not revenue.
        """
        rows = self.db.execute(
            select(
                func.coalesce(func.sum(Invoice.subtotal), 0),
                func.coalesce(func.sum(Invoice.discount_total), 0),
                func.coalesce(func.sum(Invoice.tax_total), 0),
                func.coalesce(func.sum(Invoice.total), 0),
                func.count(Invoice.id),
            ).where(
                Invoice.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
        ).one()
        return {
            "net": money(rows[0]),
            "discount": money(rows[1]),
            "tax": money(rows[2]),
            "gross": money(rows[3]),
            "invoice_count": rows[4],
            "average_invoice": money(rows[3] / rows[4]) if rows[4] else ZERO,
        }

    def purchases_summary(self, start: date | None = None, end: date | None = None) -> dict:
        """Totals for purchases, mirroring the sales summary."""
        rows = self.db.execute(
            select(
                func.coalesce(func.sum(Invoice.subtotal), 0),
                func.coalesce(func.sum(Invoice.discount_total), 0),
                func.coalesce(func.sum(Invoice.tax_total), 0),
                func.coalesce(func.sum(Invoice.total), 0),
                func.count(Invoice.id),
            ).where(
                Invoice.company_id == self.company_id,
                Invoice.kind == "purchase",
                Invoice.status != "void",
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
        ).one()
        return {
            "net": money(rows[0]),
            "discount": money(rows[1]),
            "tax": money(rows[2]),
            "gross": money(rows[3]),
            "invoice_count": rows[4],
            "average_invoice": money(rows[3] / rows[4]) if rows[4] else ZERO,
        }

    def sales_by_customer(self, start: date | None = None, end: date | None = None) -> dict:
        """Revenue per customer, with their share of the total."""
        rows = self.db.execute(
            select(
                Contact.id,
                Contact.name,
                func.coalesce(func.sum(Invoice.subtotal), 0),
                func.count(Invoice.id),
            )
            .join(Invoice, Invoice.contact_id == Contact.id)
            .where(
                Contact.company_id == self.company_id,
                Invoice.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
            .group_by(Contact.id, Contact.name)
            .order_by(func.sum(Invoice.subtotal).desc())
        ).all()

        total = sum((money(r[2]) for r in rows), ZERO)
        customers = [
            {
                "contact_id": r[0],
                "name": r[1],
                "revenue": money(r[2]),
                "invoice_count": r[3],
                "share_percent": pct(money(r[2]), total),
            }
            for r in rows
            if money(r[2]) > ZERO
        ]
        return {"customers": customers, "total": total, "count": len(customers)}

    def purchases_by_supplier(self, start: date | None = None, end: date | None = None) -> dict:
        """Spend per supplier, with their share of the total."""
        rows = self.db.execute(
            select(
                Contact.id,
                Contact.name,
                func.coalesce(func.sum(Invoice.subtotal), 0),
                func.count(Invoice.id),
            )
            .join(Invoice, Invoice.contact_id == Contact.id)
            .where(
                Contact.company_id == self.company_id,
                Invoice.company_id == self.company_id,
                Invoice.kind == "purchase",
                Invoice.status != "void",
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
            .group_by(Contact.id, Contact.name)
            .order_by(func.sum(Invoice.subtotal).desc())
        ).all()

        total = sum((money(r[2]) for r in rows), ZERO)
        suppliers = [
            {
                "contact_id": r[0],
                "name": r[1],
                "spend": money(r[2]),
                "invoice_count": r[3],
                "share_percent": pct(money(r[2]), total),
            }
            for r in rows
            if money(r[2]) > ZERO
        ]
        return {"suppliers": suppliers, "total": total, "count": len(suppliers)}

    def profit_by_source(self, start: date | None = None, end: date | None = None) -> dict:
        """Revenue and cost split by whether the goods were made or bought.

        This answers the question a factory actually asks: "did I make more on
        what I produced or on what I resold?" The split comes from each sold
        line's frozen cost, which in turn came from the layers it consumed —
        so a single invoice selling both kinds is divided correctly, and no one
        has to label anything by hand.

        Revenue follows the units, not the cost: every unit on a line sells at
        the same price, so a unit that was made earns the same as one that was
        bought. Splitting by cost would hand the cheaper-to-make units less
        revenue, which is backwards. The remainder goes to the purchased bucket
        so no cent leaks.
        """
        buckets = {
            "manufactured": {"revenue": ZERO, "cost": ZERO},
            "purchased": {"revenue": ZERO, "cost": ZERO},
            "unclassified": {"revenue": ZERO, "cost": ZERO},
        }
        per_item: dict[int, dict] = {}

        for line in self.db.scalars(self._sales_lines(start, end)):
            amounts = compute_line(line.quantity, line.unit_price, line.discount_rate, line.tax_rate)
            manufactured = money(line.cost_manufactured)
            purchased = money(line.cost_purchased)
            total_cost = manufactured + purchased

            if total_cost == ZERO:
                # A free-text line, or goods that entered stock before the
                # origin was tracked. Reported apart so it cannot distort a
                # margin that is genuinely known.
                buckets["unclassified"]["revenue"] += amounts.net
                continue

            made_qty = Decimal(str(line.quantity_manufactured or 0))
            bought_qty = Decimal(str(line.quantity_purchased or 0))
            total_qty = made_qty + bought_qty
            made_revenue = (
                (amounts.net * made_qty / total_qty).quantize(Decimal("0.01")) if total_qty else ZERO
            )
            buckets["manufactured"]["revenue"] += made_revenue
            buckets["manufactured"]["cost"] += manufactured
            buckets["purchased"]["revenue"] += amounts.net - made_revenue
            buckets["purchased"]["cost"] += purchased

            if line.item_id:
                row = per_item.setdefault(
                    line.item_id,
                    {
                        "item_id": line.item_id,
                        "revenue": ZERO,
                        "cost": ZERO,
                        "manufactured_cost": ZERO,
                        "purchased_cost": ZERO,
                        "quantity": ZERO,
                    },
                )
                row["revenue"] += amounts.net
                row["cost"] += total_cost
                row["manufactured_cost"] += manufactured
                row["purchased_cost"] += purchased
                row["quantity"] += Decimal(str(line.quantity or 0))

        items = []
        for row in per_item.values():
            item = self.items.get(row["item_id"])
            row["item_code"] = item.code
            row["item_name"] = item.name
            row["item_name_ar"] = item.name_ar
            row["profit"] = money(row["revenue"] - row["cost"])
            row["margin_percent"] = pct(row["profit"], row["revenue"])
            items.append(row)
        items.sort(key=lambda r: r["profit"], reverse=True)

        result = {}
        for key, bucket in buckets.items():
            revenue = money(bucket["revenue"])
            cost = money(bucket["cost"])
            result[key] = {
                "revenue": revenue,
                "cost": cost,
                "profit": money(revenue - cost),
                "margin_percent": pct(revenue - cost, revenue),
            }
        total_revenue = money(sum((b["revenue"] for b in result.values()), ZERO))
        total_cost = money(sum((b["cost"] for b in result.values()), ZERO))
        return {
            "manufactured": result["manufactured"],
            "purchased": result["purchased"],
            "unclassified": result["unclassified"],
            "total": {
                "revenue": total_revenue,
                "cost": total_cost,
                "profit": money(total_revenue - total_cost),
                "margin_percent": pct(total_revenue - total_cost, total_revenue),
            },
            "items": items,
        }

    def vat_report(self, start: date | None = None, end: date | None = None) -> dict:
        """Output tax less input tax, which is what is owed to the authority.

        Sales tax collected is a liability and purchase tax paid is a credit
        against it, so the two are shown apart and netted once, the way a
        return is filed.
        """
        sales = self.db.execute(
            select(
                func.coalesce(func.sum(Invoice.subtotal), 0),
                func.coalesce(func.sum(Invoice.tax_total), 0),
                func.coalesce(func.sum(Invoice.total), 0),
                func.count(Invoice.id),
            ).where(
                Invoice.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
        ).one()
        purchases = self.db.execute(
            select(
                func.coalesce(func.sum(Invoice.subtotal), 0),
                func.coalesce(func.sum(Invoice.tax_total), 0),
                func.coalesce(func.sum(Invoice.total), 0),
                func.count(Invoice.id),
            ).where(
                Invoice.company_id == self.company_id,
                Invoice.kind == "purchase",
                Invoice.status != "void",
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
        ).one()

        output_tax = money(sales[1])
        input_tax = money(purchases[1])
        return {
            "sales": {
                "net": money(sales[0]),
                "tax": output_tax,
                "gross": money(sales[2]),
                "invoice_count": sales[3],
            },
            "purchases": {
                "net": money(purchases[0]),
                "tax": input_tax,
                "gross": money(purchases[2]),
                "invoice_count": purchases[3],
            },
            "output_tax": output_tax,
            "input_tax": input_tax,
            "net_payable": money(output_tax - input_tax),
            "effective_rate": pct(output_tax, money(sales[0])),
        }

    def discount_report(self, start: date | None = None, end: date | None = None) -> dict:
        """How much was given away in discounts, by customer.

        Discount is a cost of winning the sale, so it is reported as a
        percentage of the gross it was applied to rather than as a bare total.
        """
        rows = self.db.execute(
            select(
                Contact.id,
                Contact.name,
                func.coalesce(func.sum(Invoice.subtotal + Invoice.discount_total), 0),
                func.coalesce(func.sum(Invoice.discount_total), 0),
            )
            .join(Invoice, Invoice.contact_id == Contact.id)
            .where(
                Contact.company_id == self.company_id,
                Invoice.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
                Invoice.discount_total > 0,
                *([Invoice.issue_date >= start] if start else []),
                *([Invoice.issue_date <= end] if end else []),
            )
            .group_by(Contact.id, Contact.name)
            .order_by(func.sum(Invoice.discount_total).desc())
        ).all()

        customers = []
        for contact_id, name, gross, discount in rows:
            gross, discount = money(gross), money(discount)
            customers.append(
                {
                    "contact_id": contact_id,
                    "name": name,
                    "gross": gross,
                    "discount": discount,
                    "discount_percent": pct(discount, gross),
                }
            )
        total_discount = sum((c["discount"] for c in customers), ZERO)
        total_gross = sum((c["gross"] for c in customers), ZERO)
        return {
            "customers": customers,
            "total_discount": total_discount,
            "total_gross": total_gross,
            "discount_percent": pct(total_discount, total_gross),
        }

    def monthly_trend(self, year: int | None = None) -> dict:
        """Revenue, cost of sale and gross profit month by month for one year.

        Taken from the journal, not from invoices: purchases are an asset, so
        subtracting them from sales would not be a profit. Cost of sale is what
        the goods actually cost when they left, which is the only figure that
        turns revenue into a margin.
        """
        from ...db.models import Account, JournalEntry, JournalLine

        year = year or datetime.now(UTC).date().year
        start = date(year, 1, 1)
        end = date(year, 12, 31)

        rows = self.db.execute(
            select(
                Account.code,
                func.extract("month", JournalEntry.entry_date),
                func.coalesce(func.sum(JournalLine.credit - JournalLine.debit), 0),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                Account.company_id == self.company_id,
                JournalLine.company_id == self.company_id,
                JournalEntry.company_id == self.company_id,
                Account.type.in_(("income", "expense")),
                JournalEntry.entry_date >= start,
                JournalEntry.entry_date <= end,
            )
            .group_by(Account.code, func.extract("month", JournalEntry.entry_date))
        ).all()

        months = [
            {"month": m, "revenue": ZERO, "cost_of_sales": ZERO, "expenses": ZERO}
            for m in range(1, 13)
        ]
        for code, month, net in rows:
            bucket = months[int(month) - 1]
            value = money(net)
            if code.startswith("4"):
                bucket["revenue"] += value
            elif code.startswith("5"):
                bucket["cost_of_sales"] += value
            else:
                bucket["expenses"] += value

        for bucket in months:
            bucket["gross_profit"] = money(bucket["revenue"] - bucket["cost_of_sales"])
            bucket["net_profit"] = money(bucket["gross_profit"] - bucket["expenses"])

        return {
            "year": year,
            "months": months,
            "total_revenue": sum((m["revenue"] for m in months), ZERO),
            "total_cost_of_sales": sum((m["cost_of_sales"] for m in months), ZERO),
            "total_expenses": sum((m["expenses"] for m in months), ZERO),
            "total_gross_profit": sum((m["gross_profit"] for m in months), ZERO),
            "total_net_profit": sum((m["net_profit"] for m in months), ZERO),
        }
