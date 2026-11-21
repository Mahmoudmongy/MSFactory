"""Chart of accounts endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.models import Account, JournalLine
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
    """Add a leaf account under an existing parent.

    The chart's skeleton — the five type roots and the group headings under
    them — is seeded and fixed, so this endpoint deliberately refuses to create
    another one. An open-ended "general account" is what turns a clean chart
    into a junk drawer: a second cash root, a group with no parent, an expense
    filed under assets. Every account must therefore hang off a parent of its
    own type, with a code that continues that parent's numbering.
    """
    repo = AccountRepository(db, principal.company_id)
    _validate_new_account(db, repo, payload)

    account = Account(**payload.model_dump())
    result = repo.add(account)
    db.commit()
    return result


def _validate_new_account(db: Session, repo: AccountRepository, payload: AccountCreate) -> None:
    if payload.is_group:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "group_account_not_allowed",
                "message": "لا يمكن إنشاء حساب رئيسي/تجميعي. أنشئ حساباً فرعياً تحت حساب موجود.",
            },
        )

    if payload.parent_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "parent_required",
                "message": "الحساب الجديد لازم يكون تحت حساب أب موجود في دليل الحسابات.",
            },
        )

    parent = repo.get(payload.parent_id)
    if parent.type != payload.type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "type_mismatch",
                "message": (
                    f"نوع الحساب ({payload.type}) لا يطابق نوع الحساب الأب ({parent.type})."
                ),
            },
        )

    # A child code stays inside its parent's branch: 1021 under 1020, 1011 under
    # 1010. Comparing against the parent's stem rather than the whole code is
    # what lets siblings be numbered 1021, 1022 … instead of requiring a literal
    # "1020…" prefix, while still catching a 9000 filed under cash.
    stem = parent.code[:-1]
    if not payload.code.startswith(stem):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "code_outside_parent",
                "message": f"كود الحساب لازم يكون داخل فرع الحساب الأب ({parent.code}).",
            },
        )

    existing = db.scalar(
        select(Account).where(
            Account.company_id == repo.company_id, Account.code == payload.code
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_code",
                "message": f"يوجد حساب آخر بنفس الكود ({payload.code}).",
            },
        )


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    principal: Principal = Depends(require(Permission.ACCOUNT_MANAGE)),
    db: Session = Depends(get_session),
):
    account = AccountRepository(db, principal.company_id).get(account_id)
    changes = payload.model_dump(exclude_unset=True)

    # The same rule as creation: a leaf cannot be promoted to a group heading,
    # and cannot be re-parented under a different account type. Allowing either
    # would let a PATCH sidestep the check on POST.
    if changes.get("is_group") is True and not account.is_group:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "group_account_not_allowed",
                "message": "لا يمكن تحويل حساب فرعي إلى حساب رئيسي/تجميعي.",
            },
        )
    if "parent_id" in changes and changes["parent_id"] is not None:
        new_parent = AccountRepository(db, principal.company_id).get(changes["parent_id"])
        if new_parent.type != account.type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "type_mismatch",
                    "message": "الحساب الأب الجديد من نوع مختلف عن الحساب.",
                },
            )

    for field, value in changes.items():
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


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: int,
    principal: Principal = Depends(require(Permission.ACCOUNT_MANAGE)),
    db: Session = Depends(get_session),
):
    """Delete an unused account; refuse when the ledger already references it.

    An account with postings cannot be removed without orphaning history, so the
    caller is told to deactivate it instead — the account then disappears from
    pickers while every past entry keeps its meaning.
    """
    account = AccountRepository(db, principal.company_id).get(account_id)
    used = db.scalar(
        select(func.count())
        .select_from(JournalLine)
        .where(JournalLine.account_id == account.id, JournalLine.company_id == principal.company_id)
    )
    if used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "account_in_use",
                "message": f"الحساب مستخدم في {used} قيد؛ لا يمكن حذفه. أوقفه بدلًا من ذلك.",
                "entries": int(used),
            },
        )
    db.delete(account)
    db.commit()
