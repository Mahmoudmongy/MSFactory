"""Reading the audit trail: who did what, when, in this company.

Every service already writes an ``AuditLog`` row through
``AuditRepository.record``. This service only reads them back, joined to the
acting user's name so the screen shows a person rather than an id. The trail is
append-only, so there is nothing here that writes.
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import AuditLog, User


class AuditService:
    def __init__(self, db: Session, company_id: int):
        self.db = db
        self.company_id = company_id

    def list(
        self,
        *,
        search: str = "",
        action: str = "",
        user_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """A page of the trail, newest first, with the total for paging.

        ``date_to`` is inclusive to the end of that day, because a user picking
        "to 15 November" means the whole day, not midnight at its start.
        """
        query = select(AuditLog).where(AuditLog.company_id == self.company_id)
        if search:
            like = f"%{search.strip()}%"
            query = query.where(AuditLog.summary.ilike(like))
        if action:
            query = query.where(AuditLog.action == action)
        if user_id is not None:
            query = query.where(AuditLog.user_id == user_id)
        if date_from is not None:
            query = query.where(AuditLog.created_at >= datetime.combine(date_from, time.min))
        if date_to is not None:
            query = query.where(AuditLog.created_at <= datetime.combine(date_to, time.max))

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            self.db.scalars(
                query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
            )
        )
        names = self._user_names({row.user_id for row in rows if row.user_id})
        return {
            "total": total,
            "items": [
                {
                    "id": row.id,
                    "created_at": row.created_at,
                    "user_id": row.user_id,
                    "user_name": names.get(row.user_id, "النظام"),
                    "action": row.action,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "summary": row.summary,
                }
                for row in rows
            ],
        }

    def actions(self) -> list[str]:
        """The distinct action codes present, for the filter dropdown."""
        return list(
            self.db.scalars(
                select(AuditLog.action)
                .where(AuditLog.company_id == self.company_id)
                .distinct()
                .order_by(AuditLog.action)
            )
        )

    def _user_names(self, user_ids: set[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        rows = self.db.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(user_ids))
        ).all()
        return {uid: (full_name or email or f"#{uid}") for uid, full_name, email in rows}
