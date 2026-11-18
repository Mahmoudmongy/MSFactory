"""Aggregate all v1 routers."""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    accounts,
    auth,
    contacts,
    inventory,
    invoices,
    journal,
    payments,
    production,
    reports,
    settings,
    team,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(accounts.router)
api_router.include_router(contacts.router)
api_router.include_router(inventory.router)
api_router.include_router(invoices.router)
api_router.include_router(production.router)
api_router.include_router(payments.router)
api_router.include_router(journal.router)
api_router.include_router(reports.router)
api_router.include_router(settings.router)
api_router.include_router(team.router)
