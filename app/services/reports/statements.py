"""The four statements every accountant asks for first.

Trial balance, income statement, balance sheet and cash flow. Each reads the
journal directly, so a statement can never drift from the ledger.

The response keys these methods already published are kept exactly as they
were, and the richer breakdowns are added alongside them. Renaming a key that
a screen or an export already reads is a silent breakage, and no report is
worth that.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from ...db.models import Account, JournalLine
from .base import ZERO, money, pct


class FinancialStatementsMixin:
    """Trial balance, income statement, balance sheet and cash flow."""

    def trial_balance(self, start: date | None = None, upto: date | None = None) -> dict:
        """Every account with a movement, its debit and credit totals, and its net.

        Debits and credits are shown separately as well as netted, because a
        netted-only report hides an account that was debited and credited for
        large amounts and ended near zero.
        """
        stmt = (
            select(
                Account.id,
                Account.code,
                Account.name,
                Account.name_ar,
                Account.type,
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .where(Account.company_id == self.company_id, JournalLine.company_id == self.company_id)
            .group_by(Account.id, Account.code, Account.name, Account.name_ar, Account.type)
            .order_by(Account.code)
        )
        if start or upto:
            stmt = stmt.where(JournalLine.entry_id.in_(self._entry_ids(start, upto)))

        rows = []
        total_debit = ZERO
        total_credit = ZERO
        for row in self.db.execute(stmt):
            debit, credit = money(row[5]), money(row[6])
            if debit == ZERO and credit == ZERO:
                continue
            total_debit += debit
            total_credit += credit
            rows.append(
                {
                    "account_id": row[0],
                    "code": row[1],
                    "name": row[2],
                    "name_ar": row[3],
                    "type": row[4],
                    "debit": debit,
                    "credit": credit,
                    "balance": debit - credit,
                }
            )
        return {
            "rows": rows,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balanced": total_debit == total_credit,
        }

    def income_statement(self, start: date | None = None, end: date | None = None) -> dict:
        """Revenue, cost of sales and expenses, with the resulting profit.

        Cost of sale is separated from other expenses because a factory needs
        to see gross margin apart from overheads. Every account is listed, not
        just the totals, so a surprising number can be traced to its entries.
        """
        rows = self.db.execute(
            select(
                Account.id,
                Account.code,
                Account.name,
                Account.name_ar,
                Account.type,
                func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .where(
                Account.company_id == self.company_id,
                JournalLine.company_id == self.company_id,
                Account.type.in_(("income", "expense")),
                JournalLine.entry_id.in_(self._entry_ids(start, end)),
            )
            .group_by(Account.id, Account.code, Account.name, Account.name_ar, Account.type)
            .order_by(Account.code)
        ).all()

        revenue: list[dict] = []
        cost_of_sales: list[dict] = []
        expenses: list[dict] = []
        for account_id, code, name, name_ar, account_type, net in rows:
            # Revenue is credit-normal and expenses are debit-normal, so each is
            # flipped to its own normal side. Without this an expense would
            # report negative and *add* to profit.
            value = money(-net) if account_type == "income" else money(net)
            if value == ZERO:
                continue
            entry = {
                "account_id": account_id,
                "code": code,
                "name": name,
                "name_ar": name_ar,
                "amount": value,
            }
            if account_type == "income":
                revenue.append(entry)
            elif code.startswith("5"):
                cost_of_sales.append(entry)
            else:
                expenses.append(entry)

        total_revenue = sum((r["amount"] for r in revenue), ZERO)
        total_cost = sum((r["amount"] for r in cost_of_sales), ZERO)
        total_expense = sum((r["amount"] for r in expenses), ZERO)
        gross_profit = total_revenue - total_cost
        net_profit = gross_profit - total_expense

        return {
            # Kept for callers that already read these.
            "income": total_revenue,
            "expense": total_cost + total_expense,
            "net_profit": net_profit,
            # The breakdown.
            "revenue": revenue,
            "cost_of_sales": cost_of_sales,
            "expenses": expenses,
            "total_revenue": total_revenue,
            "total_cost_of_sales": total_cost,
            "total_expenses": total_expense,
            "gross_profit": gross_profit,
            "gross_margin_percent": pct(gross_profit, total_revenue),
            "net_margin_percent": pct(net_profit, total_revenue),
        }

    def balance_sheet(self, upto: date | None = None) -> dict:
        """Assets, liabilities and equity at a date, with the accounting equation checked.

        The identity is asserted rather than assumed: ``balanced`` is False if
        the two sides disagree, which turns a silent data problem into a
        visible one.
        """
        rows = self.db.execute(
            select(
                Account.id,
                Account.code,
                Account.name,
                Account.name_ar,
                Account.type,
                func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .where(
                Account.company_id == self.company_id,
                JournalLine.company_id == self.company_id,
                Account.type.in_(("asset", "liability", "equity")),
                JournalLine.entry_id.in_(self._entry_ids(None, upto)),
            )
            .group_by(Account.id, Account.code, Account.name, Account.name_ar, Account.type)
            .order_by(Account.code)
        ).all()

        asset_rows: list[dict] = []
        liability_rows: list[dict] = []
        equity_rows: list[dict] = []
        for account_id, code, name, name_ar, account_type, net in rows:
            value = money(net)
            if value == ZERO:
                continue
            entry = {
                "account_id": account_id,
                "code": code,
                "name": name,
                "name_ar": name_ar,
                # Credit-normal accounts are flipped so a positive number means
                # "owed" or "owned" rather than "credit balance".
                "amount": value if account_type == "asset" else -value,
            }
            if account_type == "asset":
                asset_rows.append(entry)
            elif account_type == "liability":
                liability_rows.append(entry)
            else:
                equity_rows.append(entry)

        # Profit for the year so far is equity in substance even before it is
        # closed to retained earnings, so it is shown as its own line.
        net_profit = self._net_profit_to(upto)
        total_assets = sum((a["amount"] for a in asset_rows), ZERO)
        total_liabilities = sum((row["amount"] for row in liability_rows), ZERO)
        total_equity = sum((e["amount"] for e in equity_rows), ZERO) + net_profit

        return {
            # Kept for callers that already read these.
            "assets": total_assets,
            "liabilities": total_liabilities,
            "equity": total_equity - net_profit,
            "net_profit": net_profit,
            "total_equity": total_equity,
            "total_liabilities_equity": total_liabilities + total_equity,
            "balanced": total_assets == total_liabilities + total_equity,
            # The breakdown.
            "asset_rows": asset_rows,
            "liability_rows": liability_rows,
            "equity_rows": equity_rows,
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_liabilities_and_equity": total_liabilities + total_equity,
            "net_profit_for_period": net_profit,
        }

    def _net_profit_to(self, upto: date | None) -> Decimal:
        """Profit from the start of the books to a date, for the balance sheet."""
        row = self.db.execute(
            select(func.coalesce(func.sum(JournalLine.credit - JournalLine.debit), 0))
            .join(Account, Account.id == JournalLine.account_id)
            .where(
                JournalLine.company_id == self.company_id,
                Account.type.in_(("income", "expense")),
                JournalLine.entry_id.in_(self._entry_ids(None, upto)),
            )
        ).scalar()
        return money(row)

    def cash_flow(self, start: date | None = None, end: date | None = None) -> dict:
        """Where cash came from and where it went, grouped by counterpart account.

        Built from the cash accounts' own journal lines: a cash line's opposite
        side is the reason the cash moved, so grouping by the counterpart
        account names each source and use of funds without a separate
        worksheet to keep in step.
        """
        cash_ids = [a.id for a in self.accounts.list() if a.code in self.cash_codes]
        if not cash_ids:
            return {
                "inflows": [],
                "outflows": [],
                "total_in": ZERO,
                "total_out": ZERO,
                "net_change": ZERO,
                "opening": ZERO,
                "closing": ZERO,
            }

        cash_entry_ids = select(JournalLine.entry_id).where(
            JournalLine.company_id == self.company_id,
            JournalLine.account_id.in_(cash_ids),
            JournalLine.entry_id.in_(self._entry_ids(start, end)),
        )

        flows: dict[tuple[int, str], Decimal] = {}
        for line in self.db.scalars(
            select(JournalLine).where(
                JournalLine.company_id == self.company_id,
                JournalLine.entry_id.in_(cash_entry_ids),
                JournalLine.account_id.notin_(cash_ids),
            )
        ):
            account = self.accounts.get(line.account_id)
            key = (line.account_id, account.name_ar or account.name)
            # The counterpart's credit is a source of cash; its debit is a use.
            flows[key] = flows.get(key, ZERO) + money(line.credit - line.debit)

        inflows = [
            {"account_id": key[0], "name": key[1], "amount": amount}
            for key, amount in sorted(flows.items(), key=lambda kv: kv[1], reverse=True)
            if amount > ZERO
        ]
        outflows = [
            {"account_id": key[0], "name": key[1], "amount": -amount}
            for key, amount in sorted(flows.items(), key=lambda kv: kv[1])
            if amount < ZERO
        ]
        total_in = sum((i["amount"] for i in inflows), ZERO)
        total_out = sum((o["amount"] for o in outflows), ZERO)
        opening = sum((self.accounts.balance(a, upto=start) for a in cash_ids), ZERO) if start else ZERO
        closing = sum((self.accounts.balance(a, upto=end) for a in cash_ids), ZERO)

        return {
            "inflows": inflows,
            "outflows": outflows,
            "total_in": total_in,
            "total_out": total_out,
            "net_change": total_in - total_out,
            "opening": opening,
            "closing": closing,
        }
