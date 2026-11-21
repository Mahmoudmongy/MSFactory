"""FastAPI dependencies: authentication, tenant resolution and RBAC guards.

The current company is always derived from the authenticated user's membership
— never from a request header or body — which is what keeps tenants isolated.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.errors import AuthenticationError, PermissionDeniedError
from ...core.permissions import Permission, has_permission
from ...core.security import decode_token
from ...db.models import Company, Membership, User
from ...db.session import get_session, read_session

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """The authenticated caller within one tenant."""

    user: User
    company: Company
    membership: Membership

    @property
    def role(self) -> str:
        return self.membership.role

    @property
    def company_id(self) -> int:
        return self.company.id


def _user_from_credentials(credentials: HTTPAuthorizationCredentials | None) -> int:
    """Validate the bearer token and return the user id it names."""
    if not credentials:
        raise AuthenticationError("Authentication required")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise AuthenticationError("Invalid or expired access token")
    return int(payload["sub"])


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_session),
) -> User:
    user = db.get(User, _user_from_credentials(credentials))
    if not user or not user.is_active:
        raise AuthenticationError("Account is disabled")
    return user


def get_principal(
    x_company_id: int | None = Header(default=None, alias="X-Company-Id"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_session),
) -> Principal:
    """Resolve which company this request acts on.

    If the client sends ``X-Company-Id`` we verify membership for it; otherwise
    we fall back to the user's first active membership.

    User, membership and company are read in a single joined query. Against a
    remote database each statement costs a full round trip, so resolving the
    principal one row at a time added ~350ms to every authenticated request.
    """
    user_id = _user_from_credentials(credentials)
    stmt = (
        select(User, Membership, Company)
        .join(Membership, Membership.user_id == User.id)
        .join(Company, Company.id == Membership.company_id)
        .where(User.id == user_id, Membership.is_active.is_(True))
        .order_by(Membership.id)
    )
    if x_company_id is not None:
        stmt = stmt.where(Membership.company_id == x_company_id)
    row = db.execute(stmt).first()
    if row is None:
        raise PermissionDeniedError("You do not have access to this company")
    user, membership, company = row
    if not user.is_active:
        raise AuthenticationError("Account is disabled")
    if not company.is_active:
        raise PermissionDeniedError("Company is not active")
    return Principal(user=user, company=company, membership=membership)


def get_principal_readonly(
    x_company_id: int | None = Header(default=None, alias="X-Company-Id"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    """``get_principal`` for read-only endpoints, on the autocommit engine.

    Same query and same checks, but a read does not need a transaction: BEGIN
    and COMMIT cost a round trip each against the remote database, so the
    transactional path made every read pay for them.
    """
    user_id = _user_from_credentials(credentials)
    with read_session() as db:
        stmt = (
            select(User, Membership, Company)
            .join(Membership, Membership.user_id == User.id)
            .join(Company, Company.id == Membership.company_id)
            .where(User.id == user_id, Membership.is_active.is_(True))
            .order_by(Membership.id)
        )
        if x_company_id is not None:
            stmt = stmt.where(Membership.company_id == x_company_id)
        row = db.execute(stmt).first()
        if row is None:
            raise PermissionDeniedError("You do not have access to this company")
        user, membership, company = row
        if not user.is_active:
            raise AuthenticationError("Account is disabled")
        if not company.is_active:
            raise PermissionDeniedError("Company is not active")
        # Detached after the session closes; the attributes are already loaded
        # and bootstrap only reads them.
        db.expunge_all()
        return Principal(user=user, company=company, membership=membership)


def require(permission: Permission):
    """Dependency factory asserting the caller's role grants ``permission``."""

    def _guard(principal: Principal = Depends(get_principal)) -> Principal:
        if not has_permission(principal.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": f"Missing permission: {permission}"},
            )
        return principal

    return _guard
