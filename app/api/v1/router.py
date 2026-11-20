"""Aggregate all v1 routers."""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    accounts,
    audit,
    auth,
    bootstrap,
    contacts,
    documents,
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
# Registered before the resource routers so /bootstrap never collides with a
# path parameter route such as /invoices/{invoice_id}.
api_router.include_router(bootstrap.router)
api_router.include_router(accounts.router)
api_router.include_router(audit.router)
api_router.include_router(contacts.router)
api_router.include_router(documents.router)
api_router.include_router(inventory.router)
api_router.include_router(invoices.router)
api_router.include_router(production.router)
api_router.include_router(payments.router)
api_router.include_router(journal.router)
api_router.include_router(reports.router)
api_router.include_router(settings.router)
api_router.include_router(team.router)
