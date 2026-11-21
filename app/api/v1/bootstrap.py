"""One-shot bootstrap payload for the legacy screen set.

The screens need the same dozen collections on every cold start. Fetching them
one endpoint at a time meant a full authentication round trip per collection,
which against a remote database dominated startup. This endpoint authenticates
once and returns every collection the caller is allowed to read.

Each collection is read on its own connection, concurrently: the database is
remote, so a single statement costs a network round trip and running the reads
in sequence made startup scale with the number of collections.

Collections the caller's role cannot read are returned as empty lists rather
than omitted, so the client can treat the payload as a fixed shape.
"""
from __future__ import annotations

try:  # Workers-class runtimes (Pyodide/WASM) cannot start threads.
    from concurrent.futures import ThreadPoolExecutor
except ImportError:  # pragma: no cover
    ThreadPoolExecutor = None  # type: ignore[assignment]

from fastapi import APIRouter, Depends, Response

from ...core.permissions import Permission, has_permission
from ...db.session import read_session
from ...repositories.core import AccountRepository, ContactRepository
from ...repositories.documents import JournalRepository
from ...services.inventory import InventoryService
from ...services.invoices import InvoiceService
from ...services.payments import PaymentService
from ...services.production import ProductionService
from ...services.settings import SettingsService
from ...services.team import TeamService
from .deps import Principal, get_principal_readonly
from .schemas import (
    AccountOut,
    ContactOut,
    InvoiceOut,
    ItemOut,
    JournalOut,
    PaymentOut,
    SettingsOut,
    StockMovementOut,
    StockRow,
    TeamMemberOut,
    UnitOut,
    WarehouseOut,
    WorkOrderOut,
)
from .team import ROLE_LABELS, _serialize

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])

# Read-only fan-out; the pool only needs to cover the collections below.
_MAX_WORKERS = 8


def _session_payload(principal: Principal) -> dict:
    """The caller's identity, shaped like ``GET /auth/me``.

    The client used to call ``/auth/me`` and then the collections, one after the
    other. Returning the profile here lets startup authenticate in a single
    round trip; ``/auth/me`` stays for callers that only need the identity.
    """
    user = principal.user
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
        },
        "memberships": [
            {
                "company": {
                    "id": principal.company.id,
                    "name": principal.company.name,
                    "base_currency": principal.company.base_currency,
                    "country": principal.company.country,
                    "tax_number": principal.company.tax_number,
                },
                "role": principal.role,
            }
        ],
    }


def _dump(schema, rows) -> list[dict]:
    return [schema.model_validate(row).model_dump(mode="json") for row in rows]


def _read(name: str, company_id: int, user_id: int):
    """Load one collection on its own session and return it already serialised.

    Serialising inside the worker keeps every ORM object attached to the
    session that loaded it.
    """
    with read_session() as db:
        if name == "accounts":
            return _dump(AccountOut, AccountRepository(db, company_id).list())
        if name == "journal":
            return _dump(JournalOut, JournalRepository(db, company_id).list(limit=100))
        if name == "contacts":
            # Inactive contacts stay in the payload: the screens show them with a
            # "stopped" badge so they can be re-activated. Pickers filter them out.
            return _dump(
                ContactOut, ContactRepository(db, company_id).list(include_inactive=True)
            )
        if name == "invoices":
            return _dump(InvoiceOut, InvoiceService(db, company_id, user_id).list())
        if name == "payments":
            return _dump(PaymentOut, PaymentService(db, company_id, user_id).list())
        if name == "work_orders":
            return _dump(WorkOrderOut, ProductionService(db, company_id, user_id).list())
        if name == "stock":
            return _dump(StockRow, InventoryService(db, company_id, user_id).stock_on_hand())
        if name == "movements":
            return _dump(
                StockMovementOut,
                InventoryService(db, company_id, user_id).movements.query(limit=200),
            )
        if name == "snapshot":
            # Items, units and warehouses come from one service; reading them
            # together saves two round trips over the split above.
            inventory = InventoryService(db, company_id, user_id)
            return {
                "items": _dump(ItemOut, inventory.items.list(include_inactive=False)),
                "units": _dump(UnitOut, inventory.units.list(include_inactive=True)),
                "warehouses": _dump(
                    WarehouseOut, inventory.warehouses.list(include_inactive=True)
                ),
            }
        if name == "team":
            members = TeamService(db, company_id, user_id).list_members()
            return _dump(TeamMemberOut, [_serialize(m) for m in members])
        if name == "settings":
            return SettingsOut.model_validate(
                SettingsService(db, company_id, user_id).overview()
            ).model_dump(mode="json")
        raise ValueError(f"unknown collection: {name}")


def _read_all(wanted: list[str], company_id: int, user_id: int) -> dict:
    """Read the requested collections, concurrently when threads exist.

    The database is remote, so each collection costs a round trip and reading
    them in sequence makes startup scale with their number. A thread pool hides
    that latency. Runtimes that cannot start threads (Pyodide/WASM) fall back to
    the sequential loop, which is correct but slower.
    """
    if ThreadPoolExecutor is not None:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(wanted))) as pool:
            results = list(pool.map(lambda n: _read(n, company_id, user_id), wanted))
    else:  # pragma: no cover - only on threadless runtimes
        results = [_read(n, company_id, user_id) for n in wanted]

    merged: dict[str, object] = {}
    for name, value in zip(wanted, results, strict=True):
        if name == "snapshot":
            merged.update(value)
        else:
            merged[name] = value
    return merged


@router.get("", response_model=dict)
def bootstrap(
    response: Response,
    principal: Principal = Depends(get_principal_readonly),
):
    """Every collection the app needs at startup, in one authenticated call."""
    # Callers re-read this after a write; a cached copy would show pre-write rows.
    response.headers["Cache-Control"] = "no-store"
    role = principal.role
    company_id = principal.company_id
    user_id = principal.user.id

    def allowed(permission: Permission) -> bool:
        return has_permission(role, permission)

    payload: dict[str, object] = {
        "accounts": [],
        "contacts": [],
        "invoices": [],
        "payments": [],
        "journal": [],
        "items": [],
        "units": [],
        "warehouses": [],
        "stock": [],
        "movements": [],
        "work_orders": [],
        "team": [],
        "settings": None,
        "roles": [],
    }

    wanted: list[str] = []
    if allowed(Permission.REPORT_VIEW):
        wanted += ["accounts", "journal"]
    if allowed(Permission.CONTACT_VIEW):
        wanted.append("contacts")
    if allowed(Permission.INVOICE_VIEW):
        wanted.append("invoices")
    if allowed(Permission.PAYMENT_VIEW):
        wanted.append("payments")
    if allowed(Permission.ITEM_VIEW):
        wanted.append("snapshot")
    if allowed(Permission.STOCK_VIEW):
        wanted += ["stock", "movements"]
    if allowed(Permission.PRODUCTION_VIEW):
        wanted.append("work_orders")
    if allowed(Permission.USER_MANAGE):
        wanted.append("team")
    if allowed(Permission.SETTINGS_MANAGE):
        wanted.append("settings")

    if wanted:
        payload.update(_read_all(wanted, company_id, user_id))

    if allowed(Permission.USER_MANAGE):
        payload["roles"] = [
            {"value": perm.value, "label": label} for perm, label in ROLE_LABELS.items()
        ]

    payload["session"] = _session_payload(principal)
    return payload
