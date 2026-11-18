"""Chart of accounts endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting import account_balance
from ..database import get_db
from ..models import ACCOUNT_TYPES, Account
from ..schemas import AccountIn, AccountOut
from .auth import current_user

router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(current_user)])


@router.get("")
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.code)).all()
    return [
        {**AccountOut.model_validate(a).model_dump(), "balance": account_balance(db, a.id)}
        for a in accounts
    ]


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountIn, db: Session = Depends(get_db)):
    if payload.type not in ACCOUNT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid account type")
    if db.scalar(select(Account).where(Account.code == payload.code)):
        raise HTTPException(status_code=409, detail="Account code already exists")
    account = Account(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, payload: AccountIn, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    for key, value in payload.model_dump().items():
        setattr(account, key, value)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account_balance(db, account_id) != 0:
        raise HTTPException(status_code=400, detail="Cannot delete an account with a non-zero balance")
    db.delete(account)
    db.commit()
