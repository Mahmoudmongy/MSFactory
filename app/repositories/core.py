"""Tenant-scoped repository helpers.

The golden rule of this layer: every query is filtered by ``company_id``.
Repositories never accept a company id from the caller's request body; it comes
from the authenticated membership. This is what makes cross-tenant leakage a
type of bug that cannot be written by accident.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ..core.errors import ConflictError, NotFoundError
from ..db.base import utcnow
from ..db.models import (
    Account,
    CompanySetting,
    Contact,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    Sequence,
)


def scoped(stmt: Select, model, company_id: int) -> Select:
    return stmt.where(model.company_id == company_id)


class AccountRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(self, include_groups: bool = True) -> list[Account]:
        stmt = scoped(select(Account), Account, self.company_id).order_by(Account.code)
        if not include_groups:
            stmt = stmt.where(Account.is_group.is_(False))
        return list(self.db.scalars(stmt))

    def get(self, account_id: int) -> Account:
        account = self.db.scalar(
            scoped(select(Account).where(Account.id == account_id), Account, self.company_id)
        )
        if not account:
            raise NotFoundError("Account not found")
        return account

    def by_code(self, code: str) -> Account | None:
        return self.db.scalar(scoped(select(Account).where(Account.code == code), Account, self.company_id))

    def get_by_code(self, code: str) -> Account:
        account = self.by_code(code)
        if not account:
            raise NotFoundError(f"Required account {code} is missing from the chart of accounts")
        return account

    def add(self, account: Account) -> Account:
        account.company_id = self.company_id
        self.db.add(account)
        self.db.flush()
        return account

    def balance(self, account_id: int, upto: date | None = None) -> Decimal:
        return self.balance_for_accounts([account_id], upto).get(account_id, Decimal("0.00"))

    def balance_for_accounts(self, account_ids: list[int], upto: date | None = None) -> dict[int, Decimal]:
        if not account_ids:
            return {}
        stmt = (
            select(
                JournalLine.account_id,
                func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .where(JournalLine.company_id == self.company_id, JournalLine.account_id.in_(account_ids))
            .group_by(JournalLine.account_id)
        )
        if upto:
            stmt = stmt.where(
                JournalLine.entry_id.in_(
                    select(JournalEntry.id).where(
                        JournalEntry.company_id == self.company_id, JournalEntry.entry_date <= upto
                    )
                )
            )
        return {row[0]: Decimal(str(row[1])).quantize(Decimal("0.01")) for row in self.db.execute(stmt)}


class ContactRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(self, kind: str | None = None) -> list[Contact]:
        stmt = scoped(select(Contact), Contact, self.company_id).order_by(Contact.name)
        if kind:
            stmt = stmt.where(Contact.kind.in_((kind, "both")))
        return list(self.db.scalars(stmt))

    def get(self, contact_id: int) -> Contact:
        contact = self.db.scalar(
            scoped(select(Contact).where(Contact.id == contact_id), Contact, self.company_id)
        )
        if not contact:
            raise NotFoundError("Contact not found")
        return contact

    def add(self, contact: Contact) -> Contact:
        contact.company_id = self.company_id
        self.db.add(contact)
        self.db.flush()
        return contact

    def balance(self, contact_id: int) -> Decimal:
        stmt = select(
            func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0)
        ).where(JournalLine.company_id == self.company_id, JournalLine.contact_id == contact_id)
        return Decimal(str(self.db.scalar(stmt) or 0)).quantize(Decimal("0.01"))


class SequenceRepository:
    """Allocates gap-free document numbers under a row lock."""

    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def next_number(self, name: str, prefix: str = "", padding: int = 5) -> str:
        stmt = (
            select(Sequence)
            .where(Sequence.company_id == self.company_id, Sequence.name == name)
            .with_for_update()
        )
        sequence = self.db.scalar(stmt)
        if not sequence:
            sequence = Sequence(
                company_id=self.company_id, name=name, prefix=prefix, next_value=1, padding=padding
            )
            self.db.add(sequence)
            self.db.flush()
            # Re-read under lock so concurrent first-time callers serialise.
            sequence = self.db.scalar(stmt)
            if sequence is None:  # pragma: no cover - defensive
                raise ConflictError("Could not initialise document sequence")

        number = f"{sequence.prefix}-{sequence.next_value:0{sequence.padding}d}"
        sequence.next_value += 1
        self.db.flush()
        return number


class PeriodRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def for_date(self, entry_date: date) -> FiscalPeriod:
        """Fetch (or lazily create) the month period covering ``entry_date``."""
        period = self.db.scalar(
            select(FiscalPeriod).where(
                FiscalPeriod.company_id == self.company_id,
                FiscalPeriod.year == entry_date.year,
                FiscalPeriod.month == entry_date.month,
            )
        )
        if not period:
            period = FiscalPeriod(company_id=self.company_id, year=entry_date.year, month=entry_date.month)
            self.db.add(period)
            self.db.flush()
        return period

    def list(self, year: int | None = None) -> list[FiscalPeriod]:
        stmt = scoped(select(FiscalPeriod), FiscalPeriod, self.company_id).order_by(
            FiscalPeriod.year.desc(), FiscalPeriod.month.desc()
        )
        if year:
            stmt = stmt.where(FiscalPeriod.year == year)
        return list(self.db.scalars(stmt))


class SettingRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def all(self) -> dict[str, str]:
        rows = self.db.scalars(
            select(CompanySetting).where(CompanySetting.company_id == self.company_id)
        )
        return {row.key: row.value for row in rows}

    def get(self, key: str, default: str = "") -> str:
        row = self.db.scalar(
            select(CompanySetting).where(
                CompanySetting.company_id == self.company_id, CompanySetting.key == key
            )
        )
        return row.value if row else default

    def set(self, key: str, value: str) -> None:
        row = self.db.scalar(
            select(CompanySetting).where(
                CompanySetting.company_id == self.company_id, CompanySetting.key == key
            )
        )
        if row:
            row.value = value
        else:
            self.db.add(CompanySetting(company_id=self.company_id, key=key, value=value))
        self.db.flush()


class AuditRepository:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: int | None = None,
        user_id: int | None = None,
        summary: str = "",
        payload: str = "",
    ) -> None:
        from ..db.models import AuditLog

        self.db.add(
            AuditLog(
                company_id=self.company_id,
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                payload=payload,
                created_at=utcnow(),
            )
        )
        self.db.flush()
