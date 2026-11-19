"""Pydantic request/response models for the v1 API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ---
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    company_name: str = Field(min_length=1, max_length=200)
    country: str = "EG"
    base_currency: str = "EGP"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str
    is_active: bool


class CompanyOut(ORMModel):
    id: int
    name: str
    base_currency: str
    country: str
    tax_number: str = ""


class MembershipOut(BaseModel):
    company: CompanyOut
    role: str


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


# --- accounts ---
class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=160)
    name_ar: str = ""
    type: str
    parent_id: int | None = None
    is_group: bool = False
    currency: str = "EGP"
    description: str = ""


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    name_ar: str | None = None
    description: str | None = None
    is_group: bool | None = None
    currency: str | None = None
    parent_id: int | None = None


class AccountOut(ORMModel):
    id: int
    code: str
    name: str
    name_ar: str
    type: str
    parent_id: int | None
    is_group: bool
    is_active: bool
    currency: str


# --- contacts ---
class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = "customer"
    email: str = ""
    phone: str = ""
    address: str = ""
    tax_number: str = ""
    credit_limit: Decimal = Decimal("0")
    payment_terms_days: int = 30


class ContactOut(ORMModel):
    id: int
    name: str
    kind: str
    email: str
    phone: str
    tax_number: str
    is_active: bool


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    tax_number: str | None = None
    credit_limit: Decimal | None = None
    payment_terms_days: int | None = None
    is_active: bool | None = None


# --- invoices ---
class InvoiceLineIn(BaseModel):
    description: str = ""
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    discount_rate: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    account_id: int | None = None
    # Naming an item makes the line move stock; leaving it blank keeps the
    # line as free text (a service, a charge) that touches no inventory.
    item_id: int | None = None
    variant_id: int | None = None
    warehouse_id: int | None = None


class InvoiceCreate(BaseModel):
    kind: str = "sales"
    contact_id: int
    issue_date: date
    due_date: date | None = None
    notes: str = ""
    status: str = "issued"
    lines: list[InvoiceLineIn] = Field(min_length=1)


class InvoiceUpdate(BaseModel):
    kind: str | None = None
    contact_id: int | None = None
    issue_date: date | None = None
    due_date: date | None = None
    notes: str | None = None
    lines: list[InvoiceLineIn] | None = None


class ReturnLineIn(BaseModel):
    line_id: int
    quantity: Decimal = Decimal("0")


class ReturnCreate(BaseModel):
    issue_date: date
    notes: str = ""
    status: str = "issued"
    lines: list[ReturnLineIn] = Field(min_length=1)


class ReturnableLine(BaseModel):
    line_id: int
    description: str
    item_id: int | None
    quantity: Decimal
    unit_price: Decimal
    returned: Decimal
    remaining: Decimal


class InvoiceLineOut(ORMModel):
    id: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_rate: Decimal
    tax_rate: Decimal
    item_id: int | None = None
    variant_id: int | None = None
    warehouse_id: int | None = None
    # Frozen at issue time, so a past margin never changes.
    cost_amount: Decimal = Decimal("0")
    cost_manufactured: Decimal = Decimal("0")
    cost_purchased: Decimal = Decimal("0")


class InvoiceOut(ORMModel):
    id: int
    kind: str
    number: str
    contact_id: int
    issue_date: date
    due_date: date | None
    status: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal
    currency: str
    notes: str
    return_of_id: int | None = None
    lines: list[InvoiceLineOut] = []


class VoidRequest(BaseModel):
    reason: str = ""


# --- payments ---
class PaymentCreate(BaseModel):
    kind: str = "receive"
    contact_id: int
    date: date
    amount: Decimal
    bank_account_id: int
    reference: str = ""
    invoice_id: int | None = None


class AllocationOut(ORMModel):
    invoice_id: int
    amount: Decimal


class PaymentOut(ORMModel):
    id: int
    kind: str
    number: str
    contact_id: int
    date: date
    amount: Decimal
    bank_account_id: int
    reference: str
    allocations: list[AllocationOut] = []


# --- journal ---
class JournalLineIn(BaseModel):
    account_id: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    contact_id: int | None = None
    description: str = ""


class JournalCreate(BaseModel):
    entry_date: date
    memo: str = ""
    reference: str = ""
    lines: list[JournalLineIn] = Field(min_length=2)


class JournalLineOut(ORMModel):
    id: int
    account_id: int
    contact_id: int | None
    description: str
    debit: Decimal
    credit: Decimal


class JournalOut(ORMModel):
    id: int
    entry_date: date
    reference: str
    memo: str
    source_type: str
    source_id: int | None
    reverses_entry_id: int | None
    reversed_by_entry_id: int | None
    lines: list[JournalLineOut] = []


class ReverseRequest(BaseModel):
    reason: str = ""
    reversal_date: date | None = None


# --- settings & tax codes ---
class TaxCodeOut(ORMModel):
    id: int
    name: str
    name_ar: str
    rate: Decimal
    kind: str
    account_id: int | None
    is_active: bool


class SettingsOut(BaseModel):
    settings: dict[str, str]
    tax_codes: list[TaxCodeOut]


class SettingsUpdate(BaseModel):
    settings: dict[str, str] = {}


class TaxCodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    name_ar: str = ""
    rate: Decimal = Decimal("0")
    kind: str = "vat"
    account_id: int | None = None
    is_active: bool = True


class TaxCodeUpdate(BaseModel):
    name: str | None = None
    name_ar: str | None = None
    rate: Decimal | None = None
    kind: str | None = None
    account_id: int | None = None
    is_active: bool | None = None


# --- inventory: units, items, variants ---
class UnitCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    name_ar: str = ""


class UnitOut(ORMModel):
    id: int
    code: str
    name: str
    name_ar: str
    is_active: bool


class ItemGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    name_ar: str = ""
    parent_id: int | None = None


class ItemGroupOut(ORMModel):
    id: int
    name: str
    name_ar: str
    parent_id: int | None


class VariantIn(BaseModel):
    code: str = ""
    colour: str = ""
    size: str = ""
    barcode: str = ""
    attributes: str = ""


class VariantOut(ORMModel):
    id: int
    code: str
    colour: str
    size: str
    barcode: str


class ItemCreate(BaseModel):
    code: str = ""
    name: str = Field(min_length=1, max_length=200)
    name_ar: str = ""
    description: str = ""
    item_type: str = "purchased"
    item_group_id: int | None = None
    base_unit_id: int | None = None
    track_inventory: bool = True
    purchase_price: Decimal = Decimal("0")
    sale_price: Decimal = Decimal("0")
    tax_code_id: int | None = None
    reorder_level: Decimal = Decimal("0")
    default_warehouse_id: int | None = None
    image_url: str = ""
    notes: str = ""
    variants: list[VariantIn] = []


class ItemUpdate(BaseModel):
    name: str | None = None
    name_ar: str | None = None
    description: str | None = None
    item_type: str | None = None
    item_group_id: int | None = None
    base_unit_id: int | None = None
    track_inventory: bool | None = None
    purchase_price: Decimal | None = None
    sale_price: Decimal | None = None
    tax_code_id: int | None = None
    reorder_level: Decimal | None = None
    default_warehouse_id: int | None = None
    image_url: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class ItemOut(ORMModel):
    id: int
    code: str
    name: str
    name_ar: str
    item_type: str
    item_group_id: int | None
    base_unit_id: int | None
    track_inventory: bool
    has_variants: bool
    purchase_price: Decimal
    sale_price: Decimal
    reorder_level: Decimal
    is_active: bool


class ItemDetailOut(ItemOut):
    variants: list[VariantOut] = []


# --- inventory: warehouses and stock ---
class WarehouseCreate(BaseModel):
    code: str = ""
    name: str = Field(min_length=1, max_length=120)
    name_ar: str = ""
    parent_id: int | None = None
    is_group: bool = False
    kind: str = ""
    address: str = ""
    notes: str = ""


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    name_ar: str | None = None
    parent_id: int | None = None
    kind: str | None = Field(default=None, max_length=20)
    address: str | None = None
    notes: str | None = None


class WarehouseOut(ORMModel):
    id: int
    code: str
    name: str
    name_ar: str
    parent_id: int | None
    is_group: bool
    kind: str = ""
    is_active: bool


class StockRow(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    item_name_ar: str
    variant_id: int | None
    variant_code: str
    colour: str
    size: str
    unit: str
    warehouse_id: int
    warehouse_name: str
    quantity: Decimal
    unit_cost: Decimal
    value: Decimal
    reorder_level: Decimal


class StockReceive(BaseModel):
    item_id: int
    warehouse_id: int
    quantity: Decimal
    unit_cost: Decimal = Decimal("0")
    movement_date: date
    variant_id: int | None = None
    kind: str = "receipt"
    reference: str = ""
    notes: str = ""


class StockIssue(BaseModel):
    item_id: int
    warehouse_id: int
    quantity: Decimal
    movement_date: date
    variant_id: int | None = None
    kind: str = "issue"
    reference: str = ""
    notes: str = ""


class StockTransfer(BaseModel):
    item_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    quantity: Decimal
    movement_date: date
    variant_id: int | None = None
    reference: str = ""


class StockDamage(BaseModel):
    item_id: int
    warehouse_id: int
    quantity: Decimal
    movement_date: date
    variant_id: int | None = None
    reason: str = ""


class StockCount(BaseModel):
    item_id: int
    warehouse_id: int
    counted_quantity: Decimal
    movement_date: date
    variant_id: int | None = None
    notes: str = ""


class StockMovementOut(ORMModel):
    id: int
    movement_date: date
    kind: str
    item_id: int
    variant_id: int | None
    warehouse_id: int
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    reference: str
    notes: str


class InventorySummary(BaseModel):
    total_value: Decimal
    item_count: int
    warehouse_count: int
    costing_method: str


# --- production (work orders) ---
class WorkOrderMaterialIn(BaseModel):
    item_id: int
    variant_id: int | None = None
    quantity: Decimal = Decimal("0")
    notes: str = ""


class WorkOrderMaterialOut(ORMModel):
    id: int
    item_id: int
    variant_id: int | None = None
    quantity: Decimal
    cost_amount: Decimal
    notes: str


class WorkOrderCreate(BaseModel):
    item_id: int
    quantity: Decimal = Decimal("1")
    start_date: date
    warehouse_id: int | None = None
    variant_id: int | None = None
    due_date: date | None = None
    notes: str = ""
    status: str = "draft"
    materials: list[WorkOrderMaterialIn] = Field(default_factory=list)


class WorkOrderComplete(BaseModel):
    produced_quantity: Decimal | None = None


class WorkOrderCancel(BaseModel):
    reason: str = ""


class WorkOrderOut(ORMModel):
    id: int
    number: str
    status: str
    item_id: int
    variant_id: int | None = None
    warehouse_id: int
    quantity: Decimal
    produced_quantity: Decimal
    start_date: date
    due_date: date | None
    notes: str
    material_cost: Decimal
    unit_cost: Decimal
    materials: list[WorkOrderMaterialOut] = []


# --- team ---
class TeamMemberCreate(BaseModel):
    email: EmailStr
    role: str = "viewer"
    full_name: str = ""
    password: str | None = None


class TeamMemberRoleUpdate(BaseModel):
    role: str


class TeamMemberOut(BaseModel):
    id: int
    user_id: int
    email: str
    full_name: str
    role: str
    is_active: bool


class RoleOut(BaseModel):
    value: str
    label: str


# --- audit trail ---
class AuditEntryOut(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None = None
    user_name: str
    action: str
    entity_type: str
    entity_id: int | None = None
    summary: str


class AuditPage(BaseModel):
    total: int
    items: list[AuditEntryOut]
