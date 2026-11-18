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
from ...db.session import get_session

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


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_session),
) -> User:
    if not credentials:
        raise AuthenticationError("Authentication required")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise AuthenticationError("Invalid or expired access token")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise AuthenticationError("Account is disabled")
    return user


def get_principal(
    x_company_id: int | None = Header(default=None, alias="X-Company-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Principal:
    """Resolve which company this request acts on.

    If the client sends ``X-Company-Id`` we verify membership for it; otherwise
    we fall back to the user's first active membership.
    """
    stmt = select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
    if x_company_id is not None:
        stmt = stmt.where(Membership.company_id == x_company_id)
    membership = db.scalar(stmt.order_by(Membership.id))
    if not membership:
        raise PermissionDeniedError("You do not have access to this company")
    company = db.get(Company, membership.company_id)
    if not company or not company.is_active:
        raise PermissionDeniedError("Company is not active")
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
