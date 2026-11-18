"""Pydantic request/response schemas."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    username: str
    password: str


class AccountIn(BaseModel):
    code: str
    name: str
    name_ar: str = ""
    type: str
    parent_id: int | None = None
    is_group: bool = False


class AccountOut(AccountIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool


class ContactIn(BaseModel):
    name: str
    kind: str = "customer"
    email: str = ""
    phone: str = ""
    address: str = ""
    tax_number: str = ""


class ContactOut(ContactIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class InvoiceLineIn(BaseModel):
    description: str = ""
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    account_id: int | None = None


class InvoiceIn(BaseModel):
    kind: str = Field(pattern="^(sales|purchase)$")
    contact_id: int
    issue_date: date
    due_date: date | None = None
    notes: str = ""
    lines: list[InvoiceLineIn]


class PaymentIn(BaseModel):
    kind: str = Field(pattern="^(receive|spend)$")
    contact_id: int
    date: date
    amount: Decimal
    bank_account_id: int
    reference: str = ""


class JournalLineIn(BaseModel):
    account_id: int
    contact_id: int | None = None
    description: str = ""
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


class JournalIn(BaseModel):
    date: date
    reference: str = ""
    memo: str = ""
    lines: list[JournalLineIn]
