"""Accounting service: the only place journal entries are created.

Invariants enforced here:
1. Every entry is balanced (delegated to ``domain.money.validate_balanced``).
2. Every entry lands in an open fiscal period.
3. Entries are never edited or deleted; corrections are reversals.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..core.errors import ImmutableRecordError, PeriodClosedError
from ..db.base import utcnow
from ..db.models import JournalEntry, JournalLine
from ..domain.money import EntryLine, reverse_lines, to_money, validate_balanced
from ..repositories.core import AccountRepository, AuditRepository, PeriodRepository
from ..repositories.documents import JournalRepository

ZERO = Decimal("0.00")

# Journal source types are stored in English; the audit trail shows Arabic.
SOURCE_TYPE_AR = {"manual": "يدوي", "invoice": "فاتورة", "payment": "سند", "production": "إنتاج", "opening": "افتتاحي", "return": "مرتجع"}


class AccountingService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.accounts = AccountRepository(db, company_id)
        self.periods = PeriodRepository(db, company_id)
        self.journal = JournalRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)

    def post(
        self,
        *,
        entry_date: date,
        lines: list[EntryLine],
        memo: str = "",
        reference: str = "",
        source_type: str = "manual",
        source_id: int | None = None,
        reverses_entry_id: int | None = None,
    ) -> JournalEntry:
        """Create a balanced, immutable journal entry."""
        validate_balanced(lines)

        period = self.periods.for_date(entry_date)
        if period.status == "closed":
            raise PeriodClosedError(
                f"الفترة المحاسبية {period.year}-{period.month:02d} مغلقة ولا تقبل قيوداً جديدة"
            )

        entry = JournalEntry(
            company_id=self.company_id,
            entry_date=entry_date,
            period_id=period.id,
            memo=memo,
            reference=reference,
            source_type=source_type,
            source_id=source_id,
            reverses_entry_id=reverses_entry_id,
            created_by_id=self.user_id,
            created_at=utcnow(),
        )
        for line in lines:
            entry.lines.append(
                JournalLine(
                    company_id=self.company_id,
                    account_id=line.account_id,
                    contact_id=line.contact_id,
                    description=line.description,
                    debit=to_money(line.debit),
                    credit=to_money(line.credit),
                )
            )
        self.journal.add_entry(entry)
        self.audit.record(
            action="journal.post",
            entity_type="journal_entry",
            entity_id=entry.id,
            user_id=self.user_id,
            summary=f"قيد {SOURCE_TYPE_AR.get(source_type, source_type)} رقم {reference or entry.id}",
        )
        return entry

    def reverse(self, entry_id: int, *, reason: str = "", reversal_date: date | None = None) -> JournalEntry:
        """Correct an entry by posting its mirror image.

        The original is marked ``reversed_by_entry_id`` but never mutated in
        its amounts, so the audit trail stays intact.
        """
        original = self.journal.get(entry_id)
        if original.reversed_by_entry_id:
            raise ImmutableRecordError("This entry has already been reversed")

        original_lines = [
            EntryLine(
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                contact_id=line.contact_id,
                description=line.description,
            )
            for line in original.lines
        ]
        mirror = reverse_lines(original_lines)

        reversal = self.post(
            entry_date=reversal_date or original.entry_date,
            lines=mirror,
            memo=f"عكس القيد {original.id}" + (f" — {reason}" if reason else ""),
            reference=original.reference,
            source_type="reversal",
            source_id=original.id,
            reverses_entry_id=original.id,
        )
        original.reversed_by_entry_id = reversal.id
        self.audit.record(
            action="journal.reverse",
            entity_type="journal_entry",
            entity_id=original.id,
            user_id=self.user_id,
            summary=f"عُكس بالقيد {reversal.id}: {reason}",
        )
        self.db.flush()
        return reversal
