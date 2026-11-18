"""Company settings and tax code endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...core.permissions import Permission
from ...db.session import get_session
from ...services.settings import SettingsService
from ..v1.deps import Principal, require
from ..v1.schemas import SettingsOut, SettingsUpdate, TaxCodeCreate, TaxCodeOut, TaxCodeUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
def get_settings(
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    return SettingsService(db, principal.company_id, principal.user.id).overview()


@router.patch("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    service = SettingsService(db, principal.company_id, principal.user.id)
    service.update_settings(payload.settings)
    db.commit()
    return service.overview()


@router.post("/tax-codes", response_model=TaxCodeOut, status_code=status.HTTP_201_CREATED)
def create_tax_code(
    payload: TaxCodeCreate,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    code = SettingsService(db, principal.company_id, principal.user.id).create_tax_code(
        name=payload.name,
        name_ar=payload.name_ar,
        rate=payload.rate,
        kind=payload.kind,
        account_id=payload.account_id,
        is_active=payload.is_active,
    )
    db.commit()
    return TaxCodeOut.model_validate(code)


@router.patch("/tax-codes/{code_id}", response_model=TaxCodeOut)
def update_tax_code(
    code_id: int,
    payload: TaxCodeUpdate,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    code = SettingsService(db, principal.company_id, principal.user.id).update_tax_code(
        code_id, payload.model_dump(exclude_unset=True)
    )
    db.commit()
    return TaxCodeOut.model_validate(code)


@router.delete("/tax-codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tax_code(
    code_id: int,
    principal: Principal = Depends(require(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_session),
):
    SettingsService(db, principal.company_id, principal.user.id).delete_tax_code(code_id)
    db.commit()
