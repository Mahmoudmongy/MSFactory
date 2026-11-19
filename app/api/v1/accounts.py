"""Chart of accounts endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.models import Account
from ...db.session import get_session
from ...repositories.core import AccountRepository
from ..v1.deps import Principal, require
from ..v1.schemas import AccountCreate, AccountOut, AccountUpdate

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    return AccountRepository(db, principal.company_id).list()


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    principal: Principal = Depends(require(Permission.ACCOUNT_MANAGE)),
    db: Session = Depends(get_session),
):
    account = Account(**payload.model_dump())
    result = AccountRepository(db, principal.company_id).add(account)
    db.commit()
    return result


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    principal: Principal = Depends(require(Permission.ACCOUNT_MANAGE)),
    db: Session = Depends(get_session),
):
    account = AccountRepository(db, principal.company_id).get(account_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    db.commit()
    return account


@router.get("/{account_id}/balance")
def account_balance(
    account_id: int,
    principal: Principal = Depends(require(Permission.REPORT_VIEW)),
    db: Session = Depends(get_session),
):
    repo = AccountRepository(db, principal.company_id)
    repo.get(account_id)  # raises 404 for another tenant's account
    return {"account_id": account_id, "balance": repo.balance(account_id)}
