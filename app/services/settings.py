"""Company settings and tax codes.

Tax rules live in rows, not code, so a company can follow Egypt's 14% VAT or
any other country's rules without a deployment. Settings are plain key/value
pairs the UI edits directly.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import NotFoundError, ValidationError
from ..db.models import TaxCode
from ..repositories.core import AuditRepository, SettingRepository

TAX_KINDS = ("vat", "withholding", "other")

# Settings the UI may write. Anything else is rejected so a typo cannot silently
# create a dead key that nothing reads.
EDITABLE_SETTINGS = {
    "company_name",
    "currency",
    "vat_rate",
    "fiscal_year_start",
    "invoice_terms_days",
    "invoice_footer",
    "tax_number",
    "address",
    "phone",
}


class SettingsService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.settings = SettingRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)

    def tax_codes(self) -> list[TaxCode]:
        return list(
            self.db.scalars(
                select(TaxCode).where(TaxCode.company_id == self.company_id).order_by(TaxCode.id)
            )
        )

    def overview(self) -> dict:
        return {"settings": self.settings.all(), "tax_codes": self.tax_codes()}

    def update_settings(self, values: dict[str, str]) -> dict[str, str]:
        unknown = set(values) - EDITABLE_SETTINGS
        if unknown:
            raise ValidationError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        for key, value in values.items():
            self.settings.set(key, str(value))
        self.audit.record(
            action="settings.update",
            entity_type="company",
            entity_id=self.company_id,
            user_id=self.user_id,
            summary=f"Updated {', '.join(sorted(values))}",
        )
        return self.settings.all()

    def create_tax_code(
        self,
        *,
        name: str,
        name_ar: str = "",
        rate: Decimal = Decimal("0"),
        kind: str = "vat",
        account_id: int | None = None,
        is_active: bool = True,
    ) -> TaxCode:
        if kind not in TAX_KINDS:
            raise ValidationError(f"Tax kind must be one of: {', '.join(TAX_KINDS)}")
        if rate < 0:
            raise ValidationError("Tax rate cannot be negative")
        code = TaxCode(
            company_id=self.company_id,
            name=name,
            name_ar=name_ar,
            rate=rate,
            kind=kind,
            account_id=account_id,
            is_active=is_active,
        )
        self.db.add(code)
        self.db.flush()
        self.audit.record(
            action="tax_code.create",
            entity_type="tax_code",
            entity_id=code.id,
            user_id=self.user_id,
            summary=f"Created tax code {name} @ {rate}%",
        )
        return code

    def update_tax_code(self, code_id: int, values: dict) -> TaxCode:
        code = self.db.scalar(
            select(TaxCode).where(TaxCode.id == code_id, TaxCode.company_id == self.company_id)
        )
        if code is None:
            raise NotFoundError("Tax code not found")
        if "kind" in values and values["kind"] not in TAX_KINDS:
            raise ValidationError(f"Tax kind must be one of: {', '.join(TAX_KINDS)}")
        if values.get("rate") is not None and values["rate"] < 0:
            raise ValidationError("Tax rate cannot be negative")
        for key, value in values.items():
            setattr(code, key, value)
        self.db.flush()
        return code

    def delete_tax_code(self, code_id: int) -> None:
        code = self.db.scalar(
            select(TaxCode).where(TaxCode.id == code_id, TaxCode.company_id == self.company_id)
        )
        if code is None:
            raise NotFoundError("Tax code not found")
        # Retire rather than delete: issued invoices keep their historical rate.
        code.is_active = False
        self.db.flush()
