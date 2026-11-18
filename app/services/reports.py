"""Reporting service.

Every report aggregates ``journal_lines`` directly. Nothing is cached or
pre-aggregated into a "balance" column, so a report can never disagree with the
underlying entries — the classic source of accounting software bugs.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Account, JournalEntry, JournalLine
from ..repositories.core import AccountRepository
from ..repositories.documents import InvoiceRepository

ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


class ReportService:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id
        self.accounts = AccountRepository(db, company_id)
        self.invoices = InvoiceRepository(db, company_id)

    def _date_filtered_ids(self, start: date | None, end: date | None):
        stmt = select(JournalEntry.id).where(JournalEntry.company_id == self.company_id)
        if start:
            stmt = stmt.where(JournalEntry.entry_date >= start)
        if end:
            stmt = stmt.where(JournalEntry.entry_date <= end)
        return stmt

    def trial_balance(self, upto: date | None = None) -> dict:
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
            .group_by(Account.id)
            .order_by(Account.code)
        )
        if upto:
            stmt = stmt.where(JournalLine.entry_id.in_(self._date_filtered_ids(None, upto)))

        rows = []
        total_debit = ZERO
        total_credit = ZERO
        for row in self.db.execute(stmt):
            debit, credit = _money(row[5]), _money(row[6])
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
        return {"rows": rows, "total_debit": total_debit, "total_credit": total_credit}

    def income_statement(self, start: date | None = None, end: date | None = None) -> dict:
        stmt = (
            select(
                Account.type,
                func.coalesce(func.sum(JournalLine.debit), 0)
                - func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .where(
                Account.company_id == self.company_id,
                JournalLine.company_id == self.company_id,
                Account.type.in_(("income", "expense")),
            )
            .group_by(Account.type)
        )
        stmt = stmt.where(JournalLine.entry_id.in_(self._date_filtered_ids(start, end)))
        totals = {row[0]: _money(row[1]) for row in self.db.execute(stmt)}
        income = -totals.get("income", ZERO)
        expense = totals.get("expense", ZERO)
        return {"income": income, "expense": expense, "net_profit": income - expense}

    def balance_sheet(self, upto: date | None = None) -> dict:
        stmt = (
            select(
                Account.type,
                func.coalesce(func.sum(JournalLine.debit), 0)
                - func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .where(Account.company_id == self.company_id, JournalLine.company_id == self.company_id)
            .group_by(Account.type)
        )
        if upto:
            stmt = stmt.where(JournalLine.entry_id.in_(self._date_filtered_ids(None, upto)))
        totals = {row[0]: _money(row[1]) for row in self.db.execute(stmt)}

        assets = totals.get("asset", ZERO)
        liabilities = -totals.get("liability", ZERO)
        equity = -totals.get("equity", ZERO)
        net_profit = -(totals.get("income", ZERO) + totals.get("expense", ZERO))
        return {
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "net_profit": net_profit,
            "total_equity": equity + net_profit,
            "total_liabilities_equity": liabilities + equity + net_profit,
            "balanced": assets == liabilities + equity + net_profit,
        }

    def ar_aging(self, upto: date | None = None) -> dict:
        upto = upto or datetime.now(UTC).date()
        buckets = {"current": ZERO, "1_30": ZERO, "31_60": ZERO, "60_plus": ZERO}
        per_contact: dict[int, dict] = {}
        for invoice in self.invoices.outstanding_invoices("sales"):
            outstanding = _money(invoice.total) - _money(invoice.amount_paid)
            if outstanding <= ZERO:
                continue
            days = (upto - invoice.issue_date).days
            key = "current" if days <= 0 else "1_30" if days <= 30 else "31_60" if days <= 60 else "60_plus"
            buckets[key] += outstanding
            row = per_contact.setdefault(
                invoice.contact_id,
                {
                    "contact_id": invoice.contact_id,
                    "name": invoice.contact.name if invoice.contact else "",
                    "current": ZERO,
                    "1_30": ZERO,
                    "31_60": ZERO,
                    "60_plus": ZERO,
                    "total": ZERO,
                },
            )
            row[key] += outstanding
            row["total"] += outstanding
        return {"buckets": buckets, "contacts": list(per_contact.values())}

    def dashboard(self) -> dict:
        cash = ZERO
        for code in ("1010", "1020"):
            account = self.accounts.by_code(code)
            if account:
                cash += self.accounts.balance(account.id)
        income = self.income_statement()
        sheet = self.balance_sheet()
        return {
            "cash": cash,
            "income": income["income"],
            "expense": income["expense"],
            "net_profit": income["net_profit"],
            "assets": sheet["assets"],
            "liabilities": sheet["liabilities"],
        }
