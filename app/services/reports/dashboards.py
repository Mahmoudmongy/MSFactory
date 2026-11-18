"""Single-screen overviews, for the home screen and the owner's daily check.

A dashboard is not a report: it answers "is anything wrong right now" in one
glance, so it favours counts, alerts and deltas over ledgers.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ...db.models import Invoice, StockLayer
from .base import ZERO, money


class DashboardMixin:
    """The overview screens."""

    def dashboard(self) -> dict:
        """Cash, profit, receivables and payables for the current state.

        Kept deliberately small: a dashboard that needs scrolling is a report
        pretending to be a summary.
        """
        cash = sum(
            (
                self.accounts.balance(account.id)
                for account in self.accounts.list()
                if account.code in self.cash_codes
            ),
            ZERO,
        )
        income = self.income_statement()
        sheet = self.balance_sheet()
        return {
            "cash": cash,
            "income": income["income"],
            "expense": income["expense"],
            "net_profit": income["net_profit"],
            "assets": sheet["assets"],
            "liabilities": sheet["liabilities"],
            # Added alongside the original keys.
            "gross_profit": income["gross_profit"],
            "receivables": self._outstanding_total("sales"),
            "payables": self._outstanding_total("purchase"),
            "inventory_value": self._inventory_value(),
            "invoice_count": self._invoice_count(),
        }

    def _outstanding_total(self, kind: str) -> object:
        """What is still unpaid on issued invoices of one kind."""
        rows = self.db.execute(
            select(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0)).where(
                Invoice.company_id == self.company_id,
                Invoice.kind == kind,
                Invoice.status.in_(("issued", "partially_paid")),
            )
        ).scalar()
        return money(rows)

    def _inventory_value(self):
        value = self.db.execute(
            select(
                func.coalesce(func.sum(StockLayer.quantity_remaining * StockLayer.unit_cost), 0)
            ).where(StockLayer.company_id == self.company_id, StockLayer.quantity_remaining > 0)
        ).scalar()
        return money(value)

    def _invoice_count(self) -> int:
        return (
            self.db.scalar(
                select(func.count(Invoice.id)).where(
                    Invoice.company_id == self.company_id, Invoice.status != "void"
                )
            )
            or 0
        )

    def alerts(self) -> dict:
        """Everything that needs attention today, in one payload.

        Each alert is a count plus the few worst offenders, because an alert
        list that cannot be acted on from the screen is noise.
        """
        today = datetime.now(UTC).date()
        overdue = []
        for kind in ("sales", "purchase"):
            for invoice in self.invoices.outstanding_invoices(kind):
                days = (today - invoice.issue_date).days
                if days > 30:
                    overdue.append(
                        {
                            "invoice_id": invoice.id,
                            "number": invoice.number,
                            "kind": kind,
                            "party": invoice.contact.name if invoice.contact else "",
                            "outstanding": money(invoice.total) - money(invoice.amount_paid),
                            "days_overdue": days - (invoice.contact.payment_terms_days if invoice.contact else 0),
                        }
                    )
        overdue.sort(key=lambda r: r["days_overdue"], reverse=True)

        reorder = self.reorder_report()
        aging = self.ar_aging()
        return {
            "overdue_invoices": overdue[:20],
            "overdue_count": len(overdue),
            "overdue_total": sum((r["outstanding"] for r in overdue), ZERO),
            "reorder_items": reorder["items"][:20],
            "reorder_count": reorder["count"],
            "customers_over_limit": aging["over_limit"],
            "as_of": today,
        }

    def kpis(self, days: int = 30) -> dict:
        """Headline numbers for a rolling window, with the change from the one before.

        A number with no comparison cannot be acted on, so each figure carries
        the previous period and the percentage move.
        """
        end = datetime.now(UTC).date()
        start = end - timedelta(days=days)
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days)

        current = self._window_totals(start, end)
        previous = self._window_totals(previous_start, previous_end)

        def change(now, before):
            """Percentage move, or zero when there is no base to compare against."""
            if not before:
                return ZERO
            return ((now - before) / before * 100).quantize(Decimal("0.01"))

        return {
            "period_days": days,
            "start": start,
            "end": end,
            "revenue": current["revenue"],
            "revenue_change_percent": change(current["revenue"], previous["revenue"]),
            "cost_of_sales": current["cost_of_sales"],
            "gross_profit": current["gross_profit"],
            "gross_profit_change_percent": change(current["gross_profit"], previous["gross_profit"]),
            "invoice_count": current["invoice_count"],
            "invoice_count_change_percent": change(
                current["invoice_count"], previous["invoice_count"]
            ),
            "average_invoice": (
                (current["revenue"] / current["invoice_count"]).quantize(Decimal("0.01"))
                if current["invoice_count"]
                else ZERO
            ),
            "previous_revenue": previous["revenue"],
            "previous_gross_profit": previous["gross_profit"],
        }

    def _window_totals(self, start: date, end: date) -> dict:
        """Revenue, cost of sale and invoice count for one window."""
        income = self.income_statement(start=start, end=end)
        count = self.db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
                Invoice.issue_date >= start,
                Invoice.issue_date <= end,
            )
        )
        return {
            "revenue": income["total_revenue"],
            "cost_of_sales": income["total_cost_of_sales"],
            "gross_profit": income["gross_profit"],
            "invoice_count": count or 0,
        }
