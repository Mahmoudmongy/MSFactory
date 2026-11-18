"""Financial reports derived from the journal."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import ar_aging, balance_sheet, income_statement, trial_balance
from ..database import get_db
from ..models import Account, JournalEntry, JournalLine
from .auth import current_user

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(current_user)])


@router.get("/trial-balance")
def get_trial_balance(upto: date | None = None, db: Session = Depends(get_db)):
    rows = trial_balance(db, upto)
    return {
        "rows": rows,
        "total_debit": sum((r["debit"] for r in rows), 0),
        "total_credit": sum((r["credit"] for r in rows), 0),
    }


@router.get("/income-statement")
def get_income_statement(
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    return income_statement(db, start, end)


@router.get("/balance-sheet")
def get_balance_sheet(upto: date | None = None, db: Session = Depends(get_db)):
    return balance_sheet(db, upto)


@router.get("/ar-aging")
def get_ar_aging(upto: date | None = None, db: Session = Depends(get_db)):
    return ar_aging(db, upto)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    from decimal import Decimal

    from ..accounting import money

    cash = Decimal("0")
    for code in ("1200", "1210"):
        acc = db.scalar(select(Account).where(Account.code == code))
        if acc:
            from ..accounting import account_balance

            cash += account_balance(db, acc.id)
    is_result = income_statement(db)
    bs = balance_sheet(db)
    return {
        "cash": money(cash),
        "receivables": bs["assets"] - cash if bs["assets"] > cash else Decimal("0"),
        "income": is_result["income"],
        "expense": is_result["expense"],
        "net_profit": is_result["net_profit"],
        "assets": bs["assets"],
        "liabilities": bs["liabilities"],
    }


@router.get("/journal")
def journal(
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(JournalEntry).order_by(JournalEntry.date.desc(), JournalEntry.id.desc()).limit(limit)
    if start:
        stmt = stmt.where(JournalEntry.date >= start)
    if end:
        stmt = stmt.where(JournalEntry.date <= end)
    entries = db.scalars(stmt).all()
    result = []
    for entry in entries:
        result.append(
            {
                "id": entry.id,
                "date": entry.date,
                "reference": entry.reference,
                "memo": entry.memo,
                "source_type": entry.source_type,
                "lines": [
                    {
                        "account": f"{line.account.code} {line.account.name}" if line.account else "",
                        "contact": line.contact.name if line.contact else "",
                        "description": line.description,
                        "debit": line.debit,
                        "credit": line.credit,
                    }
                    for line in entry.lines
                ],
            }
        )
    return result
