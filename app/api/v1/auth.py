"""Authentication endpoints: register, login, refresh, logout, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Company, Membership, User
from ...db.session import get_session
from ...services.auth import AuthService
from ...services.onboarding import provision_company
from ..v1.deps import get_current_user
from ..v1.schemas import (
    CompanyOut,
    LoginRequest,
    MembershipOut,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_session)):
    auth = AuthService(db)
    user = auth.register(email=payload.email, password=payload.password, full_name=payload.full_name)
    company = Company(
        name=payload.company_name,
        legal_name=payload.company_name,
        country=payload.country,
        base_currency=payload.base_currency,
    )
    provision_company(db, company=company, owner_id=user.id, role="owner")
    tokens = auth.issue_tokens(user, company_id=company.id, role="owner")
    db.commit()
    return tokens


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_session)):
    auth = AuthService(db)
    user = auth.authenticate(email=payload.email, password=payload.password)
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
    )
    tokens = auth.issue_tokens(
        user,
        company_id=membership.company_id if membership else None,
        role=membership.role if membership else "",
    )
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_session)):
    tokens = AuthService(db).refresh(payload.refresh_token)
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: Session = Depends(get_session)):
    AuthService(db).logout(payload.refresh_token)
    db.commit()


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    memberships = db.scalars(
        select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
    )
    out = []
    for membership in memberships:
        company = db.get(Company, membership.company_id)
        if company:
            out.append(MembershipOut(company=CompanyOut.model_validate(company), role=membership.role))
    return MeResponse(user=UserOut.model_validate(user), memberships=out)
