"""HTTP client for the Manager.io API (``/api4``).

Manager exposes one endpoint per business object, e.g. ``/api4/customer``, and a
``-batch`` twin that pages through the whole collection. Authentication is a
query parameter, not a header::

    GET /api4/customer-batch?Key=<api-key>&Business=<business-uuid>

A batch response is an envelope ``{"items": [{"key": <uuid>, "item": {...}}]}``;
the ``key`` is what the mapping table stores.

Only ``GET`` is implemented. This importer reads from Manager and writes to our
own books through our own services, so nothing here can modify the source data.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..core.errors import ValidationError

# Manager Cloud/Server answer on this host; a self-hosted server passes its own
# base URL instead, which is why the caller always supplies one.
DEFAULT_BASE_URL = "https://cloud.manager.io"
PAGE_SIZE = 200
# A single page must not hang a request thread forever.
REQUEST_TIMEOUT = 30


class ManagerError(ValidationError):
    """The Manager side answered with an error, or could not be reached."""


class ManagerAuthError(ManagerError):
    """The key was rejected. Retrying will fail the same way for every entity."""


@dataclass(frozen=True)
class ManagerCredentials:
    base_url: str
    api_key: str
    business: str


class ManagerClient:
    def __init__(self, credentials: ManagerCredentials):
        self.credentials = credentials

    # ------------------------------------------------------------------ http

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = {"Key": self.credentials.api_key, "Business": self.credentials.business}
        query.update({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.credentials.base_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"

        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # Manager returns {"error": "...", "message": "..."} on 4xx.
            body = error.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
                detail = payload.get("message") or payload.get("error") or body
            except json.JSONDecodeError:
                detail = body
            if error.code in (401, 403):
                raise ManagerAuthError("رفض Manager الطلب: تحقق من مفتاح الـ API وصلاحياته.") from error
            if error.code == 404:
                raise ManagerError("لم يجد Manager هذا العنصر. تأكد من معرّف الشركة (Business).") from error
            raise ManagerError(f"خطأ من Manager ({error.code}): {detail}") from error
        except urllib.error.URLError as error:
            raise ManagerError(f"تعذّر الوصول إلى Manager على {self.credentials.base_url}: {error.reason}") from error
        except TimeoutError as error:
            raise ManagerError("انتهت مهلة الاتصال بـ Manager.") from error

    # ------------------------------------------------------------- endpoints

    def businesses(self) -> list[dict[str, Any]]:
        """The businesses (companies) this API key can read.

        Manager returns this list at the top level, without a Business scope,
        so it is the natural first call: it tells the user which id to import.
        """
        payload = self._get("/api4/business-batch", {"PageSize": PAGE_SIZE})
        return _items(payload)

    def customers(self) -> list[dict[str, Any]]:
        return self._batch("/api4/customer-batch")

    def suppliers(self) -> list[dict[str, Any]]:
        return self._batch("/api4/supplier-batch")

    def accounts(self) -> list[dict[str, Any]]:
        return self._batch("/api4/balance-sheet-account-batch")

    def profit_and_loss_accounts(self) -> list[dict[str, Any]]:
        return self._batch("/api4/profit-and-loss-statement-account-batch")

    def bank_and_cash_accounts(self) -> list[dict[str, Any]]:
        return self._batch("/api4/bank-or-cash-account-batch")

    def tax_codes(self) -> list[dict[str, Any]]:
        return self._batch("/api4/tax-code-batch")

    def inventory_items(self) -> list[dict[str, Any]]:
        return self._batch("/api4/inventory-item-batch")

    def non_inventory_items(self) -> list[dict[str, Any]]:
        return self._batch("/api4/non-inventory-item-batch")

    def sales_invoices(self) -> list[dict[str, Any]]:
        return self._batch("/api4/sales-invoice-batch")

    def purchase_invoices(self) -> list[dict[str, Any]]:
        return self._batch("/api4/purchase-invoice-batch")

    def receipts(self) -> list[dict[str, Any]]:
        return self._batch("/api4/receipt-batch")

    def payments(self) -> list[dict[str, Any]]:
        return self._batch("/api4/payment-batch")

    def _batch(self, path: str) -> list[dict[str, Any]]:
        """Page through a ``-batch`` endpoint until it stops returning items.

        Manager has no documented total count, so paging stops when a page comes
        back short. The guard on ``Skip`` keeps a server that ignores ``Skip``
        from looping forever.
        """
        collected: list[dict[str, Any]] = []
        skip = 0
        seen = 0
        while True:
            payload = self._get(path, {"Skip": skip, "PageSize": PAGE_SIZE})
            page = _items(payload)
            if not page:
                break
            collected.extend(page)
            skip += len(page)
            if len(page) < PAGE_SIZE:
                break
            if skip <= seen:  # the server ignored Skip; stop rather than spin
                break
            seen = skip
        return collected


def _items(payload: Any) -> list[dict[str, Any]]:
    """Flatten a Manager batch envelope into ``[{key, item}, ...]``.

    A single-object endpoint answers with the object itself; both shapes are
    accepted so the same client works against either.
    """
    if isinstance(payload, list):
        return [_entry(row) for row in payload]
    if isinstance(payload, dict):
        rows = payload.get("items")
        if isinstance(rows, list):
            return [_entry(row) for row in rows]
        if "key" in payload or "item" in payload:
            return [_entry(payload)]
    return []


def _entry(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {"key": "", "item": {}}
    item = row.get("item") if isinstance(row.get("item"), dict) else row
    key = row.get("key") or item.get("key") or item.get("id") or ""
    return {"key": str(key), "item": item}
