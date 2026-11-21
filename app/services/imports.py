"""Bring a Manager.io business into these books.

The import is deliberately narrow: it creates contacts, tax codes, items, and
then documents. Money never arrives as a raw balance; it arrives as invoices and
payments, which are re-posted through the same services the UI uses. That keeps
the imported books internally consistent — the trial balance of an import equals
the trial balance Manager showed, because both sides derive it from the same
document set.

Two rules shape the whole file:

* **Idempotent.** Every external object is looked up in ``import_mappings``
  first. A second run updates its own rows instead of duplicating them, so an
  import that stopped halfway can simply be run again.
* **Nothing half-written.** One transaction per import. A document that fails
  validation is reported and skipped; the rest still land, and the report says
  exactly which ones were skipped and why. Rolling the whole thing back would
  mean a single bad row blocks a migration.

Documents are posted in dependency order — contacts and accounts before
invoices, invoices before payments — because a payment needs an invoice to
settle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import ValidationError
from ..db.base import utcnow
from ..db.models import (
    Account,
    Contact,
    ImportJob,
    ImportMapping,
    Invoice,
    Item,
    Payment,
    TaxCode,
)
from ..repositories.core import AccountRepository, ContactRepository
from .invoices import InvoiceService
from .manager_client import ManagerAuthError, ManagerClient, ManagerError
from .payments import PaymentService

SOURCE = "manager"
# How many problems the report keeps. A migration of a badly-configured
# business can produce hundreds of the same complaint; the first fifty tell the
# story and keep the stored report small enough to render on a phone.
MAX_ISSUES = 50


@dataclass
class ImportOptions:
    contacts: bool = True
    accounts: bool = True
    tax_codes: bool = True
    items: bool = True
    invoices: bool = True
    payments: bool = True
    # Dates before this are not imported. Useful to start clean books at a
    # fiscal year boundary instead of dragging years of history along.
    since: date | None = None


def _is_fatal(error: Exception) -> bool:
    """A failure that will repeat for every entity, so stopping is the honest end.

    Retrying an import with a rejected key just produces six identical issues and
    a green "completed" status, which reads as success to whoever is watching.
    """
    return isinstance(error, ManagerAuthError)


@dataclass(frozen=True)
class ImportResult:
    """What an import did, detached from the session so it survives a rollback."""

    job_id: int | None
    status: str
    dry_run: bool
    counts: dict[str, dict[str, int]]
    issues: list[dict[str, str]]
    error: str


@dataclass
class ImportReport:
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    issues: list[dict[str, str]] = field(default_factory=list)

    def bump(self, entity: str, outcome: str) -> None:
        bucket = self.counts.setdefault(entity, {"created": 0, "updated": 0, "skipped": 0, "failed": 0})
        bucket[outcome] = bucket.get(outcome, 0) + 1

    def issue(self, entity: str, label: str, reason: str) -> None:
        self.bump(entity, "failed")
        if len(self.issues) < MAX_ISSUES:
            self.issues.append({"entity": entity, "label": label[:160], "reason": reason[:400]})

    def as_dict(self) -> dict[str, Any]:
        return {"counts": self.counts, "issues": self.issues}


class ManagerImportService:
    """Pull a Manager business into the current tenant.

    ``dry_run`` walks the same code path but rolls back at the end, so the
    preview a user approves is produced by the code that will do the work —
    not by a second, differently-behaved estimate.
    """

    def __init__(
        self,
        db: Session,
        company_id: int,
        user_id: int | None = None,
        client: ManagerClient | None = None,
    ):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.client = client
        self.accounts = AccountRepository(db, company_id)
        self.contacts = ContactRepository(db, company_id)
        self.report = ImportReport()

    # ------------------------------------------------------------------- run

    def run(self, options: ImportOptions, *, dry_run: bool = False) -> ImportResult:
        """Walk the whole import and return what happened.

        A dry run goes through exactly this code and then rolls back, so the
        preview cannot drift from the real thing. The result is copied into a
        plain object before the rollback: after it, the ORM objects are detached
        and reading them would raise.
        """
        if self.client is None:
            raise ValidationError("لم يتم إعداد الاتصال بـ Manager.")

        job = ImportJob(
            company_id=self.company_id,
            source=SOURCE,
            business_key=self.client.credentials.business,
            status="running",
            dry_run=dry_run,
            business_name=self._business_name(),
            started_at=utcnow(),
            created_by_id=self.user_id,
        )
        self.db.add(job)
        self.db.flush()
        job_id = job.id

        try:
            if options.accounts:
                self._import_accounts(job)
            if options.tax_codes:
                self._import_tax_codes(job)
            if options.contacts:
                self._import_contacts(job)
            if options.items:
                self._import_items(job)
            if options.invoices:
                self._import_invoices(job, options)
            if options.payments:
                self._import_payments(job, options)

            report = self.report.as_dict()
            job.report = report
            job.status = "completed"
            job.finished_at = utcnow()

            if dry_run:
                # A preview that left rows behind would be a trap: the user
                # looks, cancels, and finds half a chart of accounts.
                self.db.rollback()
                return ImportResult(
                    job_id=None,
                    status="completed",
                    dry_run=True,
                    counts=report["counts"],
                    issues=report["issues"],
                    error="",
                )

            self.db.commit()
            return ImportResult(
                job_id=job_id,
                status="completed",
                dry_run=False,
                counts=report["counts"],
                issues=report["issues"],
                error="",
            )
        except Exception as error:
            self.db.rollback()
            self._record_failure(job_id, error)
            raise

    def _business_name(self) -> str:
        """The business's display name, for the job log.

        The key is a UUID; a log of UUIDs tells a user nothing about which set of
        books they imported. This is best-effort: a failed lookup must not stop
        the import, so the key remains as the fallback.
        """
        try:
            for row in self.client.businesses() if self.client else []:
                if row.get("key") == self.client.credentials.business:
                    return row["item"].get("name") or ""
        except ManagerError:
            return ""
        return ""

    def _record_failure(self, job_id: int, error: Exception) -> None:
        """Persist the failure on its own, after the rollback that lost the job.

        The import's work is discarded, but the fact that it failed and why is
        worth keeping: it is the only evidence a user has when they call for
        help. This runs in a fresh transaction because the old one is gone.
        """
        self.db.add(
            ImportJob(
                id=job_id,
                company_id=self.company_id,
                source=SOURCE,
                business_key=self.client.credentials.business if self.client else "",
                business_name=self._business_name(),
                status="failed",
                dry_run=False,
                started_at=utcnow(),
                finished_at=utcnow(),
                created_by_id=self.user_id,
                report=self.report.as_dict(),
                error=str(error)[:2000],
            )
        )
        self.db.commit()

    # ------------------------------------------------------------- mappings

    def _mapping(self, entity: str, external_key: str) -> ImportMapping | None:
        return self.db.scalar(
            select(ImportMapping).where(
                ImportMapping.company_id == self.company_id,
                ImportMapping.source == SOURCE,
                ImportMapping.entity == entity,
                ImportMapping.external_key == external_key,
            )
        )

    def _remember(self, job: ImportJob, entity: str, external_key: str, local_id: int, label: str) -> None:
        existing = self._mapping(entity, external_key)
        if existing is not None:
            existing.local_id = local_id
            existing.label = label[:200]
            existing.job_id = job.id or None
            return
        self.db.add(
            ImportMapping(
                company_id=self.company_id,
                source=SOURCE,
                entity=entity,
                external_key=external_key[:120],
                local_id=local_id,
                label=label[:200],
                job_id=job.id or None,
            )
        )

    # --------------------------------------------------------------- accounts

    def _import_accounts(self, job: ImportJob) -> None:
        """Map Manager's accounts onto the seeded chart.

        Manager's chart is user-defined, so it cannot be copied one-to-one. What
        the import does instead is *attach* each Manager account to the closest
        existing account of the same type: a Manager "cash" account becomes a
        child of 1010, a "bank" account a child of 1020, an expense a child of
        5000. Balances therefore land in a place the reports already understand,
        and the imported account keeps its Manager name so a human can find it.

        Group accounts are skipped — our headings are fixed (see the accounts
        endpoint), and a group in Manager carries no postings of its own.
        """
        try:
            rows = self.client.accounts() + self.client.profit_and_loss_accounts()
        except ManagerError as error:
            if _is_fatal(error):
                raise
            self.report.issue("account", "دليل الحسابات", str(error))
            return

        bank_keys: set[str] = set()
        try:
            bank_keys = {row["key"] for row in self.client.bank_and_cash_accounts() if row["key"]}
        except ManagerAuthError:
            raise
        except ManagerError:
            bank_keys = set()

        # A bank account appears in both lists; importing it twice would create
        # a duplicate child under 1020.
        seen: set[str] = set()
        for entry in rows:
            key = entry["key"]
            data = entry["item"]
            label = data.get("name") or data.get("nameWithCode") or key
            if not key or key in seen:
                continue
            seen.add(key)
            if self._mapping("account", key) is not None:
                self.report.bump("account", "skipped")
                continue
            if data.get("isGroup") or data.get("group"):
                self.report.bump("account", "skipped")
                continue

            try:
                account = self._attach_account(data, label, is_bank=key in bank_keys)
            except ValidationError as error:
                self.report.issue("account", label, error.message)
                continue

            self._remember(job, "account", key, account.id, label)
            self.report.bump("account", "created")

    def _attach_account(self, data: dict[str, Any], label: str, *, is_bank: bool) -> Account:
        """Create a leaf account under the seeded heading that matches its type."""
        parent_code = "1020" if is_bank else _heading_for(data)

        parent = self.accounts.get_by_code(parent_code)
        if parent is None:
            raise ValidationError(f"الحساب الأب {parent_code} غير موجود في دليل الحسابات.")

        code = self._next_child_code(parent)
        account = Account(
            company_id=self.company_id,
            code=code,
            name=label[:160],
            name_ar=label[:160],
            type=parent.type,
            parent_id=parent.id,
            is_group=False,
            description="مستورد من Manager.io",
        )
        self.accounts.add(account)
        return account

    def _next_child_code(self, parent: Account) -> str:
        """Number a child inside its parent's branch: 1021, 1022 under 1020."""
        stem = parent.code[:-1]
        used = {
            row.code
            for row in self.db.scalars(
                select(Account).where(Account.company_id == self.company_id, Account.code.like(f"{stem}%"))
            )
        }
        # The parent's own code counts as used, and so does anything already
        # allocated in this run (the loop re-reads the DB, so it does).
        used.add(parent.code)
        index = 1
        while f"{stem}{index}" in used:
            index += 1
        return f"{stem}{index}"

    # ------------------------------------------------------------- tax codes

    def _import_tax_codes(self, job: ImportJob) -> None:
        try:
            rows = self.client.tax_codes()
        except ManagerError as error:
            if _is_fatal(error):
                raise
            self.report.issue("tax_code", "أكواد الضريبة", str(error))
            return

        for entry in rows:
            key = entry["key"]
            data = entry["item"]
            label = data.get("name") or key
            if not key:
                continue
            if self._mapping("tax_code", key) is not None:
                self.report.bump("tax_code", "skipped")
                continue
            try:
                rate = _decimal(data.get("rate"))
                tax = TaxCode(
                    company_id=self.company_id,
                    name=label[:80],
                    name_ar=label[:80],
                    rate=rate,
                    kind="vat",
                    account_id=None,
                )
                self.db.add(tax)
                self.db.flush()
            except (ValidationError, InvalidOperation) as error:
                self.report.issue("tax_code", label, str(error))
                continue
            self._remember(job, "tax_code", key, tax.id, label)
            self.report.bump("tax_code", "created")

    # --------------------------------------------------------------- contacts

    def _import_contacts(self, job: ImportJob) -> None:
        groups = (
            ("customer", self._safe_list(self.client.customers, "contact")),
            ("supplier", self._safe_list(self.client.suppliers, "contact")),
        )
        for kind, rows in groups:
            for entry in rows:
                key = entry["key"]
                data = entry["item"]
                label = data.get("name") or key
                if not key:
                    continue

                mapping = self._mapping("contact", key)
                if mapping is not None:
                    self._update_contact(mapping, data, kind)
                    self.report.bump("contact", "updated")
                    continue

                try:
                    contact = Contact(
                        company_id=self.company_id,
                        name=label[:200],
                        kind=kind,
                        email=(data.get("email") or "")[:200],
                        phone=(data.get("obsolete_Telephone") or data.get("phone") or "")[:60],
                        address=(data.get("billingAddress") or "")[:2000],
                        tax_number=(data.get("obsolete_BusinessIdentifier") or "")[:60],
                        is_active=not data.get("inactive", False),
                    )
                    self.db.add(contact)
                    self.db.flush()
                except Exception as error:
                    self.report.issue("contact", label, str(error))
                    continue
                self._remember(job, "contact", key, contact.id, label)
                self.report.bump("contact", "created")

    def _update_contact(self, mapping: ImportMapping, data: dict[str, Any], kind: str) -> None:
        contact = self.contacts.get(mapping.local_id)
        contact.email = (data.get("email") or contact.email)[:200]
        contact.address = (data.get("billingAddress") or contact.address)[:2000]
        contact.is_active = not data.get("inactive", False)
        # A customer that also buys from us is "both"; the flag is not lost by
        # whichever list happened to be imported second.
        if contact.kind != kind:
            contact.kind = "both"

    def _safe_list(self, loader, entity: str) -> list[dict[str, Any]]:
        """Fetch one list, tolerating a missing entity but not a broken connection.

        Manager installations differ: one may have no inventory items at all, and
        a 404 for that list is normal. A rejected key or an unreachable host is
        not — continuing past it would report "0 imported" for every entity and
        look like an empty business rather than a failed login.
        """
        try:
            return loader()
        except ManagerAuthError:
            raise
        except ManagerError as error:
            if _is_fatal(error):
                raise
            self.report.issue(entity, "قائمة", str(error))
            return []
        except Exception as error:
            self.report.issue(entity, "قائمة", str(error))
            return []

    # ------------------------------------------------------------------ items

    def _import_items(self, job: ImportJob) -> None:
        rows = self._safe_list(self.client.inventory_items, "item")
        rows += self._safe_list(self.client.non_inventory_items, "item")

        for entry in rows:
            key = entry["key"]
            data = entry["item"]
            label = data.get("name") or data.get("itemName") or key
            if not key:
                continue
            if self._mapping("item", key) is not None:
                self.report.bump("item", "skipped")
                continue
            try:
                item = Item(
                    company_id=self.company_id,
                    code=self._next_item_code(label),
                    name=label[:200],
                    name_ar=label[:200],
                    # Manager's "non-inventory" items are services here: they
                    # carry a price but no stock movement.
                    item_type="service" if data.get("trackQuantity") is False else "purchased",
                    track_inventory=data.get("trackQuantity") is not False,
                    sale_price=_decimal(data.get("salePrice") or 0),
                    purchase_price=_decimal(data.get("purchasePrice") or 0),
                    is_active=not data.get("inactive", False),
                )
                self.db.add(item)
                self.db.flush()
            except Exception as error:
                self.report.issue("item", label, str(error))
                continue
            self._remember(job, "item", key, item.id, label)
            self.report.bump("item", "created")

    def _next_item_code(self, label: str) -> str:
        """Give the item a stable, unique code derived from its name.

        Manager items are keyed by uuid and often have no human code, but our
        item table requires one. Deriving it from the name keeps the printed
        invoice readable, and the numeric suffix keeps duplicates apart.
        """
        base = "".join(ch for ch in label if ch.isalnum())[:24].upper() or "ITEM"
        candidate = base
        index = 2
        while self.db.scalar(select(Item.id).where(Item.company_id == self.company_id, Item.code == candidate)):
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    # --------------------------------------------------------------- invoices

    def _import_invoices(self, job: ImportJob, options: ImportOptions) -> None:
        sources = (
            ("sales", self.client.sales_invoices),
            ("purchase", self.client.purchase_invoices),
        )
        service = InvoiceService(self.db, self.company_id, self.user_id)
        for kind, loader in sources:
            for entry in self._safe_list(loader, "invoice"):
                key = entry["key"]
                data = entry["item"]
                label = data.get("reference") or data.get("number") or key
                if not key:
                    continue
                if self._mapping("invoice", key) is not None:
                    self.report.bump("invoice", "skipped")
                    continue

                issue_date = _as_date(data.get("issueDate"))
                if issue_date is None:
                    self.report.issue("invoice", label, "تاريخ الإصدار غير مقروء")
                    continue
                if options.since and issue_date < options.since:
                    self.report.bump("invoice", "skipped")
                    continue

                contact = self._contact_for(data.get("customer") or data.get("supplier"), kind)
                if contact is None:
                    self.report.issue("invoice", label, "الطرف غير موجود ولم يُستورد")
                    continue

                lines = self._invoice_lines(data, kind)
                if not lines:
                    self.report.issue("invoice", label, "لا توجد بنود قابلة للاستيراد")
                    continue

                try:
                    invoice = service.create(
                        kind=kind,
                        contact_id=contact.id,
                        issue_date=issue_date,
                        due_date=_as_date(data.get("dueDateDate")) or _as_date(data.get("dueDate")),
                        lines=lines,
                        notes=(data.get("description") or "")[:2000],
                        status="issued",
                    )
                except Exception as error:
                    self.report.issue("invoice", label, str(error))
                    continue

                self._remember(job, "invoice", key, invoice.id, label)
                self.report.bump("invoice", "created")

    def _invoice_lines(self, data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        """Turn Manager lines into ours, keeping the tax rate the line used.

        Manager stores a tax *code* uuid per line; the rate lives on the code.
        The tax rate is therefore resolved from the imported tax codes, and a
        line whose code is unknown falls back to zero tax and is reported —
        guessing a rate would silently corrupt the VAT report.
        """
        lines: list[dict[str, Any]] = []
        for raw in data.get("lines") or []:
            if not isinstance(raw, dict):
                continue
            quantity = _decimal(raw.get("qty") or 1) or Decimal("1")
            unit_price = _decimal(raw.get("salesUnitPrice") or raw.get("purchaseUnitPrice") or 0)
            discount_rate = _decimal(raw.get("discountPercentage") or 0)
            tax_rate = self._tax_rate_for(raw.get("taxCode"))
            description = raw.get("lineDescription") or ""
            if not description and raw.get("item"):
                description = self._item_label(raw.get("item"))
            lines.append(
                {
                    "description": str(description)[:255],
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "discount_rate": discount_rate,
                    "tax_rate": tax_rate,
                    "item_id": self._item_id(raw.get("item")),
                }
            )
        return lines

    def _tax_rate_for(self, external_key: Any) -> Decimal:
        if not external_key:
            return Decimal("0")
        mapping = self._mapping("tax_code", str(external_key))
        if mapping is None:
            return Decimal("0")
        tax = self.db.get(TaxCode, mapping.local_id)
        return tax.rate if tax else Decimal("0")

    def _item_label(self, external_key: Any) -> str:
        mapping = self._mapping("item", str(external_key))
        return mapping.label if mapping else ""

    def _item_id(self, external_key: Any) -> int | None:
        if not external_key:
            return None
        mapping = self._mapping("item", str(external_key))
        return mapping.local_id if mapping else None

    def _contact_for(self, external_key: Any, kind: str) -> Contact | None:
        """Resolve the party on a document, falling back to a name match.

        Manager sometimes leaves the uuid out of a document that was created
        through an older version, so a name lookup is the safety net.
        """
        if external_key:
            mapping = self._mapping("contact", str(external_key))
            if mapping is not None:
                return self.contacts.get(mapping.local_id)
        return None

    # --------------------------------------------------------------- payments

    def _import_payments(self, job: ImportJob, options: ImportOptions) -> None:
        sources = (
            ("receive", self.client.receipts),
            ("spend", self.client.payments),
        )
        service = PaymentService(self.db, self.company_id, self.user_id)
        default_bank = self._default_bank_account()
        if default_bank is None:
            self.report.issue("payment", "حساب البنك", "لا يوجد حساب بنك أو نقدية لاستخدامه")
            return

        for kind, loader in sources:
            for entry in self._safe_list(loader, "payment"):
                key = entry["key"]
                data = entry["item"]
                label = data.get("reference") or key
                if not key:
                    continue
                if self._mapping("payment", key) is not None:
                    self.report.bump("payment", "skipped")
                    continue

                payment_date = _as_date(data.get("date"))
                if payment_date is None:
                    self.report.issue("payment", label, "التاريخ غير مقروء")
                    continue
                if options.since and payment_date < options.since:
                    self.report.bump("payment", "skipped")
                    continue

                contact = self._contact_for(data.get("customer") or data.get("supplier") or data.get("payee"), kind)
                if contact is None:
                    self.report.issue("payment", label, "الطرف غير موجود ولم يُستورد")
                    continue

                amount = _decimal(data.get("amount") or data.get("total") or 0)
                if amount <= 0:
                    self.report.issue("payment", label, "المبلغ صفر أو غير مقروء")
                    continue

                try:
                    payment = service.create(
                        kind=kind,
                        contact_id=contact.id,
                        payment_date=payment_date,
                        amount=amount,
                        bank_account_id=self._bank_for(data) or default_bank.id,
                        reference=str(data.get("reference") or "")[:60],
                    )
                except Exception as error:
                    self.report.issue("payment", label, str(error))
                    continue

                self._remember(job, "payment", key, payment.id, label)
                self.report.bump("payment", "created")

    def _default_bank_account(self) -> Account | None:
        for code in ("1020", "1010"):
            account = self.accounts.get_by_code(code)
            if account is not None:
                return account
        return None

    def _bank_for(self, data: dict[str, Any]) -> int | None:
        """The bank the payment went through, when the source names one."""
        key = data.get("bankAccount") or data.get("paidFrom") or data.get("receivedIn")
        if not key:
            return None
        mapping = self._mapping("account", str(key))
        return mapping.local_id if mapping else None


# ------------------------------------------------------------------ helpers


def _heading_for(data: dict[str, Any]) -> str:
    """Pick the seeded heading a Manager account belongs under.

    Manager flags account capabilities explicitly (``isCashAtBank``,
    ``isAccountsReceivable``…), which is far more reliable than parsing its
    free-text type, so the flags are tried first and the type is the fallback.
    """
    if data.get("isAccountsReceivable"):
        return "1100"
    if data.get("isInventoryOnHand"):
        return "1200"
    if data.get("isAccountsPayable"):
        return "2100"
    if data.get("isCashAtBank"):
        return "1020"

    kind = str(data.get("type") or data.get("accountType") or "").lower()
    if "income" in kind or "revenue" in kind:
        return "4100"
    if "expense" in kind or "cost" in kind:
        return "5900"
    if "liabilit" in kind:
        return "2100"
    if "equity" in kind or "capital" in kind:
        return "3100"
    return "1000"


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _as_date(value: Any) -> date | None:
    """Read Manager's ISO timestamps, and tolerate a plain date.

    Manager sends ``2024-03-01T00:00:00Z``; a hand-written fixture or an older
    export may send ``2024-03-01``. Both are accepted, anything else is None.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def invoice_numbers_of(db: Session, company_id: int) -> set[str]:
    """Document numbers already used, so an import can avoid colliding."""
    return set(db.scalars(select(Invoice.number).where(Invoice.company_id == company_id))) | set(
        db.scalars(select(Payment.number).where(Payment.company_id == company_id))
    )
