"""Team management: invite users into a company and manage their roles.

Adding a member creates the user account if the email is new, then links it to
the company with a role. The membership row is the tenant boundary, so a user
who belongs to several companies keeps one membership per company.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.permissions import Role
from ..db.models import Membership, User
from ..repositories.core import AuditRepository
from .auth import AuthService


class TeamService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.audit = AuditRepository(db, company_id)

    def list_members(self) -> list[Membership]:
        return list(
            self.db.scalars(
                select(Membership)
                .where(Membership.company_id == self.company_id)
                .order_by(Membership.id)
            )
        )

    def add_member(
        self,
        *,
        email: str,
        role: str,
        full_name: str = "",
        password: str | None = None,
    ) -> Membership:
        email = email.strip().lower()
        if role not in {r.value for r in Role}:
            raise ValidationError("دور غير معروف")

        user = self.db.scalar(select(User).where(User.email == email))
        if user is None:
            if not password:
                raise ValidationError("كلمة مرور مطلوبة للمستخدم الجديد")
            user = AuthService(self.db).register(email=email, password=password, full_name=full_name)
        elif full_name:
            user.full_name = full_name

        existing = self.db.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.company_id == self.company_id,
            )
        )
        if existing:
            raise ConflictError("المستخدم عضو في الشركة بالفعل")

        membership = Membership(user_id=user.id, company_id=self.company_id, role=role)
        self.db.add(membership)
        self.db.flush()
        self.audit.record(
            action="member.add",
            entity_type="membership",
            entity_id=membership.id,
            user_id=self.user_id,
            summary=f"Added {email} as {role}",
        )
        return membership

    def change_role(self, membership_id: int, role: str) -> Membership:
        if role not in {r.value for r in Role}:
            raise ValidationError("دور غير معروف")
        membership = self._get(membership_id)
        self._guard_owner(membership)
        membership.role = role
        self.db.flush()
        self.audit.record(
            action="member.role",
            entity_type="membership",
            entity_id=membership.id,
            user_id=self.user_id,
            summary=f"Changed role to {role}",
        )
        return membership

    def deactivate(self, membership_id: int) -> Membership:
        membership = self._get(membership_id)
        self._guard_owner(membership)
        membership.is_active = False
        self.db.flush()
        self.audit.record(
            action="member.deactivate",
            entity_type="membership",
            entity_id=membership.id,
            user_id=self.user_id,
            summary="Deactivated member",
        )
        return membership

    def _get(self, membership_id: int) -> Membership:
        membership = self.db.scalar(
            select(Membership).where(
                Membership.id == membership_id,
                Membership.company_id == self.company_id,
            )
        )
        if not membership:
            raise NotFoundError("العضوية غير موجودة")
        return membership

    def _guard_owner(self, membership: Membership) -> None:
        # Removing the last owner would lock the company out of administration.
        if membership.role != Role.OWNER.value:
            return
        owners = self.db.scalars(
            select(Membership).where(
                Membership.company_id == self.company_id,
                Membership.role == Role.OWNER.value,
                Membership.is_active.is_(True),
            )
        ).all()
        if len(owners) <= 1:
            raise ConflictError("لا يمكن إزالة أو تغيير دور المالك الأخير")
