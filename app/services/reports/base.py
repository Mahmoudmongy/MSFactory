"""Shared plumbing for every report.

Reports are split into mixins by subject (financial statements, ledger,
parties, inventory, sales, tax) and composed into one ``ReportService``. The
split is about keeping files readable, not about behaviour: every mixin shares
the same session, company scope and helpers from here.

One rule runs through all of them: a report reads ``journal_lines`` and
``stock_movements`` directly and never a cached total. A stored balance can
drift from the entries that produced it, and a report that disagrees with the
ledger is worse than no report at all.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.models import Account, JournalEntry, JournalLine
from ...repositories.core import AccountRepository, ContactRepository
from ...repositories.documents import InvoiceRepository, JournalRepository, PaymentRepository
from ...repositories.inventory import ItemRepository, StockLayerRepository, WarehouseRepository

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Coerce a database value into a 2-decimal Decimal.

    Rows come back as Decimal already, but aggregates can arrive as None or as
    a numeric type with more scale than money should carry.
    """
    return Decimal(str(value or 0)).quantize(CENT)


def pct(part: Decimal, whole: Decimal) -> Decimal:
    """A percentage, or zero when the denominator is zero rather than an error."""
    if not whole:
        return ZERO
    return (part / whole * 100).quantize(CENT)


class ReportBase:
    """Session, company scope and the repositories every report reaches for."""

    #: Accounts treated as cash and bank for cash-flow purposes.
    cash_codes: tuple[str, ...] = ("1010", "1020")

    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id
        self.accounts = AccountRepository(db, company_id)
        self.contacts = ContactRepository(db, company_id)
        self.invoices = InvoiceRepository(db, company_id)
        self.payments = PaymentRepository(db, company_id)
        self.journal = JournalRepository(db, company_id)
        self.items = ItemRepository(db, company_id)
        self.warehouses = WarehouseRepository(db, company_id)
        self.layers = StockLayerRepository(db, company_id)

    # ------------------------------------------------------------------ scope

    def _entry_ids(self, start: date | None, end: date | None):
        """Ids of the entries inside a date window, as a subquery.

        Filtering the journal by entry id rather than joining keeps each report
        free to group however it likes without a second join to ``entries``.
        """
        stmt = select(JournalEntry.id).where(JournalEntry.company_id == self.company_id)
        if start:
            stmt = stmt.where(JournalEntry.entry_date >= start)
        if end:
            stmt = stmt.where(JournalEntry.entry_date <= end)
        return stmt

    def _journal_lines(self, start: date | None, end: date | None):
        """Scoped ``journal_lines`` query limited to a date window."""
        stmt = select(JournalLine).where(JournalLine.company_id == self.company_id)
        if start or end:
            stmt = stmt.where(JournalLine.entry_id.in_(self._entry_ids(start, end)))
        return stmt

    def _balances_by_type(self, start: date | None, end: date | None) -> dict[str, Decimal]:
        """Net debit per account type, so callers can flip the credit-normal ones."""
        rows = self.db.execute(
            select(
                Account.type,
                func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0),
            )
            .join(Account, Account.id == JournalLine.account_id)
            .where(
                JournalLine.company_id == self.company_id,
                JournalLine.entry_id.in_(self._entry_ids(start, end)),
            )
            .group_by(Account.type)
        ).all()
        return {row[0]: money(row[1]) for row in rows}
