"""Role-based access control.

Roles are hierarchical in spirit but expressed as explicit permission sets so a
deployment can add roles without touching code. Permissions are coarse and
resource-oriented (``invoice:create``); fine-grained rules (e.g. "can only see
their own customer") are enforced in the service layer.
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ACCOUNTANT = "accountant"
    SALES = "sales"
    STOREKEEPER = "storekeeper"  # أمين مخزن
    VIEWER = "viewer"
    CUSTOMER = "customer"  # portal-only, sees just their own documents


class Permission(StrEnum):
    COMPANY_MANAGE = "company:manage"
    USER_MANAGE = "user:manage"
    ACCOUNT_MANAGE = "account:manage"
    CONTACT_VIEW = "contact:view"
    CONTACT_MANAGE = "contact:manage"
    ITEM_VIEW = "item:view"
    ITEM_MANAGE = "item:manage"
    WAREHOUSE_MANAGE = "warehouse:manage"
    STOCK_VIEW = "stock:view"
    STOCK_MANAGE = "stock:manage"
    INVOICE_VIEW = "invoice:view"
    INVOICE_MANAGE = "invoice:manage"
    PAYMENT_VIEW = "payment:view"
    PAYMENT_MANAGE = "payment:manage"
    JOURNAL_MANAGE = "journal:manage"
    REPORT_VIEW = "report:view"
    PERIOD_MANAGE = "period:manage"
    AUDIT_VIEW = "audit:view"
    SETTINGS_MANAGE = "settings:manage"


_ALL = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.OWNER: _ALL,
    Role.ADMIN: _ALL,
    Role.ACCOUNTANT: {
        Permission.ACCOUNT_MANAGE,
        Permission.CONTACT_VIEW,
        Permission.CONTACT_MANAGE,
        Permission.ITEM_VIEW,
        Permission.ITEM_MANAGE,
        Permission.WAREHOUSE_MANAGE,
        Permission.STOCK_VIEW,
        Permission.STOCK_MANAGE,
        Permission.INVOICE_VIEW,
        Permission.INVOICE_MANAGE,
        Permission.PAYMENT_VIEW,
        Permission.PAYMENT_MANAGE,
        Permission.JOURNAL_MANAGE,
        Permission.REPORT_VIEW,
        Permission.PERIOD_MANAGE,
        Permission.AUDIT_VIEW,
    },
    Role.SALES: {
        Permission.CONTACT_VIEW,
        Permission.CONTACT_MANAGE,
        Permission.ITEM_VIEW,
        Permission.STOCK_VIEW,
        Permission.INVOICE_VIEW,
        Permission.INVOICE_MANAGE,
        Permission.PAYMENT_VIEW,
        Permission.REPORT_VIEW,
    },
    Role.STOREKEEPER: {
        Permission.ITEM_VIEW,
        Permission.ITEM_MANAGE,
        Permission.WAREHOUSE_MANAGE,
        Permission.STOCK_VIEW,
        Permission.STOCK_MANAGE,
        Permission.CONTACT_VIEW,
        Permission.REPORT_VIEW,
    },
    Role.VIEWER: {
        Permission.CONTACT_VIEW,
        Permission.ITEM_VIEW,
        Permission.STOCK_VIEW,
        Permission.INVOICE_VIEW,
        Permission.PAYMENT_VIEW,
        Permission.REPORT_VIEW,
    },
    Role.CUSTOMER: set(),
}


def permissions_for(role: str) -> set[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return set()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
