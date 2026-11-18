"""The books themselves: the day book, the ledger, and account activity.

These are the reports an accountant uses to answer "show me the entries behind
that number". They read the journal in entry order and carry a running
balance, which is the only way to see a balance build up rather than just
landing on a figure.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ...db.models import Account, JournalEntry, JournalLine
from .base import ZERO, money


class LedgerMixin:
    """General journal, general ledger and account activity."""

    def journal_book(self, start: date | None = None, end: date | None = None, limit: int = 500) -> dict:
        """Every entry in date order with its lines, for the general journal.

        Debits and credits are listed per line so an entry can be read the way
        it was written, and the entry's own balance is asserted: an entry that
        does not balance is a data fault and is flagged rather than hidden.
        """
        stmt = (
            select(JournalEntry)
            .where(JournalEntry.company_id == self.company_id)
            .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
            .limit(limit)
        )
        if start:
            stmt = stmt.where(JournalEntry.entry_date >= start)
        if end:
            stmt = stmt.where(JournalEntry.entry_date <= end)

        entries = []
        for entry in self.db.scalars(stmt):
            lines = []
            debit_total = ZERO
            credit_total = ZERO
            for line in entry.lines:
                debit, credit = money(line.debit), money(line.credit)
                debit_total += debit
                credit_total += credit
                lines.append(
                    {
                        "account_id": line.account_id,
                        "account_code": line.account.code if line.account else "",
                        "account_name": (line.account.name_ar or line.account.name) if line.account else "",
                        "contact_name": line.contact.name if line.contact else "",
                        "description": line.description,
                        "debit": debit,
                        "credit": credit,
                    }
                )
            entries.append(
                {
                    "id": entry.id,
                    "entry_date": entry.entry_date,
                    "reference": entry.reference,
                    "memo": entry.memo,
                    "source_type": entry.source_type,
                    "lines": lines,
                    "total_debit": debit_total,
                    "total_credit": credit_total,
                    "balanced": debit_total == credit_total,
                }
            )
        return {"entries": entries, "count": len(entries)}

    def general_ledger(self, start: date | None = None, end: date | None = None) -> dict:
        """Every account's movements and closing balance, in one pass.

        Accounts with no movement in the window are left out so the report
        stays readable on a large chart, but every account that moved appears
        with its own running balance.
        """
        rows = self.db.execute(
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
            .where(
                Account.company_id == self.company_id,
                JournalLine.company_id == self.company_id,
                JournalLine.entry_id.in_(self._entry_ids(start, end)),
            )
            .group_by(Account.id, Account.code, Account.name, Account.name_ar, Account.type)
            .order_by(Account.code)
        ).all()

        accounts = []
        total_debit = ZERO
        total_credit = ZERO
        for account_id, code, name, name_ar, account_type, debit, credit in rows:
            debit, credit = money(debit), money(credit)
            if debit == ZERO and credit == ZERO:
                continue
            total_debit += debit
            total_credit += credit
            accounts.append(
                {
                    "account_id": account_id,
                    "code": code,
                    "name": name,
                    "name_ar": name_ar,
                    "type": account_type,
                    "opening": self._opening_balance(account_id, start),
                    "debit": debit,
                    "credit": credit,
                    "closing": self.accounts.balance(account_id, upto=end),
                }
            )
        return {
            "accounts": accounts,
            "total_debit": total_debit,
            "total_credit": total_credit,
        }

    def _opening_balance(self, account_id: int, start: date | None) -> Decimal:
        """The balance carried into a period, signed debit-minus-credit.

        ``balance(upto=...)`` includes entries on the date given, so the
        opening must stop the day *before* the window starts; otherwise the
        first day's movements would be counted twice, once as opening and once
        as activity.
        """
        if not start:
            return ZERO
        return self.accounts.balance(account_id, upto=start - timedelta(days=1))

    def account_activity(self, account_id: int, start: date | None = None, end: date | None = None) -> dict:
        """One account's lines with a running balance, oldest first.

        The running balance is what makes a ledger useful: it shows when a
        balance crossed a threshold, not merely that it ended there.
        """
        account = self.accounts.get(account_id)
        stmt = (
            select(JournalLine)
            .where(
                JournalLine.company_id == self.company_id,
                JournalLine.account_id == account_id,
                JournalLine.entry_id.in_(self._entry_ids(start, end)),
            )
            .order_by(JournalLine.entry_id, JournalLine.id)
        )

        running = self._opening_balance(account_id, start)
        opening = running
        movements = []
        for line in self.db.scalars(stmt):
            debit, credit = money(line.debit), money(line.credit)
            running += debit - credit
            movements.append(
                {
                    "id": line.id,
                    "entry_id": line.entry_id,
                    "date": line.entry.entry_date if line.entry else None,
                    "reference": line.entry.reference if line.entry else "",
                    "memo": line.entry.memo if line.entry else "",
                    "description": line.description,
                    "contact_name": line.contact.name if line.contact else "",
                    "debit": debit,
                    "credit": credit,
                    "balance": running,
                }
            )

        return {
            "account": {
                "id": account.id,
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar,
                "type": account.type,
            },
            "opening": opening,
            "movements": movements,
            "closing": running,
            "total_debit": sum((m["debit"] for m in movements), ZERO),
            "total_credit": sum((m["credit"] for m in movements), ZERO),
        }

    def account_balances(self) -> dict:
        """Every account with its current balance, grouped by type.

        Used to populate an account picker and to show a chart of accounts
        with live balances rather than just names.
        """
        accounts = self.accounts.list()
        balances = self.accounts.balance_for_accounts([a.id for a in accounts])
        grouped: dict[str, list[dict]] = {}
        for account in accounts:
            grouped.setdefault(account.type, []).append(
                {
                    "id": account.id,
                    "code": account.code,
                    "name": account.name,
                    "name_ar": account.name_ar,
                    "is_group": account.is_group,
                    "parent_id": account.parent_id,
                    "balance": balances.get(account.id, ZERO),
                }
            )
        return {"by_type": grouped, "count": len(accounts)}
