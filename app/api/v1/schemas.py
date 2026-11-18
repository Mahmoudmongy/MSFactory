"""Pydantic request/response models for the v1 API."""
from __future__ import annotations

from datetime import date
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


# --- invoices ---
class InvoiceLineIn(BaseModel):
    description: str = ""
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    discount_rate: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    account_id: int | None = None


class InvoiceCreate(BaseModel):
    kind: str = "sales"
    contact_id: int
    issue_date: date
    due_date: date | None = None
    notes: str = ""
    status: str = "issued"
    lines: list[InvoiceLineIn] = Field(min_length=1)


class InvoiceLineOut(ORMModel):
    id: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_rate: Decimal
    tax_rate: Decimal


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
