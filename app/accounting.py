"""Double-entry accounting engine: posting, balances and financial reports.

Every business document (invoice, payment, manual entry) is turned into a
balanced journal entry here. Reports read only from journal lines, so the
books always reconcile.
"""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import DEBIT_TYPES, Account, Invoice, JournalEntry, JournalLine

ZERO = Decimal("0")


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def account_by_code(db: Session, code: str) -> Account | None:
    return db.scalar(select(Account).where(Account.code == code))


def next_number(db: Session, model, kind_field: str, kind: str, prefix: str) -> str:
    count = db.scalar(select(func.count()).select_from(model).where(getattr(model, kind_field) == kind)) or 0
    return f"{prefix}-{count + 1:05d}"


def post_entry(
    db: Session,
    entry_date: date,
    lines: list[dict],
    memo: str = "",
    reference: str = "",
    source_type: str = "manual",
    source_id: int | None = None,
) -> JournalEntry:
    """Create a balanced journal entry.

    ``lines`` is a list of dicts with keys: account_id, debit, credit and the
    optional contact_id / description.
    """
    lines = [line for line in lines if money(line.get("debit")) or money(line.get("credit"))]
    if not lines:
        raise HTTPException(status_code=400, detail="Journal entry needs at least one line")

    debit_total = sum((money(line.get("debit")) for line in lines), ZERO)
    credit_total = sum((money(line.get("credit")) for line in lines), ZERO)
    if debit_total != credit_total:
        raise HTTPException(
            status_code=400,
            detail=f"Entry is not balanced: debits {debit_total} != credits {credit_total}",
        )

    entry = JournalEntry(
        date=entry_date, memo=memo, reference=reference, source_type=source_type, source_id=source_id
    )
    for line in lines:
        entry.lines.append(
            JournalLine(
                account_id=line["account_id"],
                contact_id=line.get("contact_id"),
                description=line.get("description", ""),
                debit=money(line.get("debit")),
                credit=money(line.get("credit")),
            )
        )
    db.add(entry)
    db.flush()
    return entry


def delete_entry(db: Session, entry_id: int | None) -> None:
    if not entry_id:
        return
    entry = db.get(JournalEntry, entry_id)
    if entry:
        db.delete(entry)


def _entry_filter(stmt, start: date | None, end: date | None):
    if not (start or end):
        return stmt
    ids = select(JournalEntry.id)
    if start:
        ids = ids.where(JournalEntry.date >= start)
    if end:
        ids = ids.where(JournalEntry.date <= end)
    return stmt.where(JournalLine.entry_id.in_(ids))


def account_balance(db: Session, account_id: int, upto: date | None = None) -> Decimal:
    """Net balance of an account (debit positive, credit negative)."""
    stmt = select(
        func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0)
    ).where(JournalLine.account_id == account_id)
    if upto:
        stmt = stmt.where(
            JournalLine.entry_id.in_(select(JournalEntry.id).where(JournalEntry.date <= upto))
        )
    return money(db.scalar(stmt))


def contact_balance(db: Session, contact_id: int, upto: date | None = None) -> Decimal:
    """Debit positive. For customers this is what they still owe us."""
    stmt = select(
        func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0)
    ).where(JournalLine.contact_id == contact_id)
    if upto:
        stmt = stmt.where(
            JournalLine.entry_id.in_(select(JournalEntry.id).where(JournalEntry.date <= upto))
        )
    return money(db.scalar(stmt))


def trial_balance(db: Session, upto: date | None = None) -> list[dict]:
    stmt = (
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.name_ar,
            Account.type,
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .group_by(Account.id)
        .order_by(Account.code)
    )
    stmt = _entry_filter(stmt, None, upto)

    rows = []
    for row in db.execute(stmt):
        debit, credit = money(row.debit), money(row.credit)
        if debit == ZERO and credit == ZERO:
            continue
        rows.append(
            {
                "account_id": row.id,
                "code": row.code,
                "name": row.name,
                "name_ar": row.name_ar,
                "type": row.type,
                "debit": debit,
                "credit": credit,
                "balance": debit - credit,
            }
        )
    return rows


def income_statement(db: Session, start: date | None = None, end: date | None = None) -> dict:
    stmt = (
        select(
            Account.type,
            func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .where(Account.type.in_(("income", "expense")))
        .group_by(Account.type)
    )
    stmt = _entry_filter(stmt, start, end)
    totals = {row[0]: money(row[1]) for row in db.execute(stmt)}
    income = -totals.get("income", ZERO)  # income carries a credit balance
    expense = totals.get("expense", ZERO)
    return {"income": income, "expense": expense, "net_profit": income - expense}


def balance_sheet(db: Session, upto: date | None = None) -> dict:
    totals = {t: ZERO for t in DEBIT_TYPES | {"liability", "equity", "income"}}
    for row in trial_balance(db, upto):
        totals[row["type"]] = totals.get(row["type"], ZERO) + row["balance"]
    assets = totals["asset"]
    liabilities = -totals["liability"]
    equity = -totals["equity"]
    net_profit = -(totals["income"] + totals["expense"])
    return {
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "net_profit": net_profit,
        "total_equity": equity + net_profit,
        "total_liabilities_equity": liabilities + equity + net_profit,
        "balanced": assets == liabilities + equity + net_profit,
    }


def ar_aging(db: Session, upto: date | None = None) -> dict:
    """Outstanding customer invoices grouped by age bucket."""
    upto = upto or date.today()
    invoices = db.scalars(
        select(Invoice).where(Invoice.kind == "sales", Invoice.status.in_(("issued", "paid")))
    ).all()
    buckets = {"current": ZERO, "1_30": ZERO, "31_60": ZERO, "60_plus": ZERO}
    per_contact: dict[int, dict] = {}
    for inv in invoices:
        outstanding = money(inv.total) - money(inv.amount_paid)
        if outstanding <= ZERO:
            continue
        days = (upto - inv.issue_date).days
        key = "current" if days <= 0 else "1_30" if days <= 30 else "31_60" if days <= 60 else "60_plus"
        buckets[key] += outstanding
        row = per_contact.setdefault(
            inv.contact_id,
            {"contact_id": inv.contact_id, "name": inv.contact.name, "current": ZERO,
             "1_30": ZERO, "31_60": ZERO, "60_plus": ZERO, "total": ZERO},
        )
        row[key] += outstanding
        row["total"] += outstanding
    return {"buckets": buckets, "contacts": list(per_contact.values())}
