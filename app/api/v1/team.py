"""Team (membership) endpoints: list, invite, change role, deactivate."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission, Role
from ...db.session import get_session
from ...services.team import TeamService
from ..v1.deps import Principal, require
from ..v1.schemas import RoleOut, TeamMemberCreate, TeamMemberOut, TeamMemberRoleUpdate

router = APIRouter(prefix="/team", tags=["team"])

ROLE_LABELS = {
    Role.OWNER: "مالك",
    Role.ADMIN: "مدير",
    Role.ACCOUNTANT: "محاسب",
    Role.SALES: "مبيعات",
    Role.STOREKEEPER: "أمين مخزن",
    Role.VIEWER: "مشاهدة فقط",
    Role.CUSTOMER: "عميل (بوابة)",
}


def _serialize(membership) -> TeamMemberOut:
    user = membership.user
    return TeamMemberOut(
        id=membership.id,
        user_id=membership.user_id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        is_active=membership.is_active,
    )


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: Principal = Depends(require(Permission.USER_MANAGE))):
    return [RoleOut(value=role.value, label=label) for role, label in ROLE_LABELS.items()]


@router.get("", response_model=list[TeamMemberOut])
def list_members(
    principal: Principal = Depends(require(Permission.USER_MANAGE)),
    db: Session = Depends(get_session),
):
    service = TeamService(db, principal.company_id, principal.user.id)
    return [_serialize(m) for m in service.list_members()]


@router.post("", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    payload: TeamMemberCreate,
    principal: Principal = Depends(require(Permission.USER_MANAGE)),
    db: Session = Depends(get_session),
):
    service = TeamService(db, principal.company_id, principal.user.id)
    membership = service.add_member(
        email=payload.email,
        role=payload.role,
        full_name=payload.full_name,
        password=payload.password,
    )
    db.commit()
    return _serialize(membership)


@router.patch("/{membership_id}", response_model=TeamMemberOut)
def change_role(
    membership_id: int,
    payload: TeamMemberRoleUpdate,
    principal: Principal = Depends(require(Permission.USER_MANAGE)),
    db: Session = Depends(get_session),
):
    service = TeamService(db, principal.company_id, principal.user.id)
    membership = service.change_role(membership_id, payload.role)
    db.commit()
    return _serialize(membership)


@router.delete("/{membership_id}", response_model=TeamMemberOut)
def deactivate_member(
    membership_id: int,
    principal: Principal = Depends(require(Permission.USER_MANAGE)),
    db: Session = Depends(get_session),
):
    service = TeamService(db, principal.company_id, principal.user.id)
    membership = service.deactivate(membership_id)
    db.commit()
    return _serialize(membership)
