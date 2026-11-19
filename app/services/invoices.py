"""Invoice service: create, issue and void sales/purchase invoices.

An invoice becomes an accounting fact when issued: at that moment a balanced
journal entry is posted and linked back to the document. Voiding posts a
reversal instead of deleting anything.

When a line names a stocked item the invoice also moves stock:

* a sales line issues the goods and books cost of sale, so the margin on that
  line is real rather than assumed;
* a purchase line receives the goods and opens a FIFO layer at the price paid.

The cost is frozen onto the line at that moment. That is what makes a past
profit stable: repricing a purchase later can never rewrite an earlier sale.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..core.errors import ImmutableRecordError, ValidationError
from ..db.base import utcnow
from ..db.models import (
    AP_CONTROL_CODE,
    AR_CONTROL_CODE,
    COGS_CODE,
    INVENTORY_CODE,
    RETURN_KINDS,
    SALES_CODE,
    VAT_PAYABLE_CODE,
    Invoice,
    InvoiceLine,
)
from ..domain.money import EntryLine, compute_line, summarize, to_money
from ..repositories.core import AccountRepository, AuditRepository, ContactRepository, SequenceRepository
from ..repositories.documents import InvoiceRepository
from .accounting import AccountingService
from .inventory import InventoryService

ZERO = Decimal("0.00")

# Invoice kinds and payment directions are stored in English; the audit trail
# is read by Arabic speakers, so it shows the Arabic word.
KIND_AR = {"sales": "فاتورة بيع", "purchase": "فاتورة شراء", "sales_return": "مرتجع بيع", "purchase_return": "مرتجع شراء"}


class InvoiceService:
    def __init__(self, db: Session, company_id: int, user_id: int | None = None):
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.invoices = InvoiceRepository(db, company_id)
        self.accounts = AccountRepository(db, company_id)
        self.contacts = ContactRepository(db, company_id)
        self.sequences = SequenceRepository(db, company_id)
        self.audit = AuditRepository(db, company_id)
        self.accounting = AccountingService(db, company_id, user_id)
        self.inventory = InventoryService(db, company_id, user_id)

    def list(self, kind: str | None = None, contact_id: int | None = None) -> list[Invoice]:
        return self.invoices.list(kind=kind, contact_id=contact_id)

    def returns_of(self, invoice_id: int) -> list[Invoice]:
        return self.invoices.returns_of(invoice_id)

    def returnable(self, invoice_id: int) -> list[dict]:
        """Per line of an invoice, how much has been returned and how much remains.

        A return reduces what can be returned again, so a second return of the
        same line cannot exceed what is left.
        """
        invoice = self.invoices.get(invoice_id)
        already: dict[int, Decimal] = {}
        for ret in self.invoices.returns_of(invoice_id):
            if ret.status == "void":
                continue
            for line in ret.lines:
                if line.source_line_id:
                    already[line.source_line_id] = already.get(line.source_line_id, ZERO) + Decimal(
                        str(line.quantity or 0)
                    )
        rows = []
        for line in invoice.lines:
            returned = already.get(line.id, ZERO)
            remaining = Decimal(str(line.quantity or 0)) - returned
            rows.append(
                {
                    "line_id": line.id,
                    "description": line.description,
                    "item_id": line.item_id,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "discount_rate": line.discount_rate,
                    "tax_rate": line.tax_rate,
                    "returned": returned,
                    "remaining": remaining,
                }
            )
        return rows

    def create_return(
        self,
        *,
        invoice_id: int,
        issue_date: date,
        lines: list[dict],
        notes: str = "",
        status: str = "issued",
    ) -> Invoice:
        """Create a return (credit note) against an issued invoice.

        A return is the mirror of the invoice: it credits what was sold, or
        debits what was bought, and it puts the goods back (or takes them out
        again) at the cost frozen on the original line. Nothing about the
        original invoice is changed, so the history stays intact.
        """
        original = self.invoices.get(invoice_id)
        if original.kind not in ("sales", "purchase"):
            raise ValidationError("لا يمكن عمل مرتجع لمرتجع")
        if original.status not in ("issued", "partially_paid", "paid"):
            raise ValidationError("يمكن عمل مرتجع لفاتورة مُصدَرة فقط")

        if not lines:
            raise ValidationError("المرتجع يحتاج بنداً واحداً على الأقل")

        remaining = {row["line_id"]: row for row in self.returnable(invoice_id)}
        computed: list = []
        prepared: list[dict] = []
        for raw in lines:
            line_id = raw.get("line_id")
            row = remaining.get(line_id)
            if row is None:
                raise ValidationError("بند المرتجع لا يخص هذه الفاتورة")
            quantity = Decimal(str(raw.get("quantity", 0) or 0))
            if quantity <= 0:
                continue
            if quantity > row["remaining"]:
                raise ValidationError(
                    f"الكمية المرتجعة {quantity} أكبر من المتاح {row['remaining']} للبند «{row['description']}»"
                )
            amounts = compute_line(
                quantity, row["unit_price"], row["discount_rate"], row["tax_rate"]
            )
            computed.append(amounts)
            prepared.append({"row": row, "quantity": quantity})

        if not prepared:
            raise ValidationError("أدخل كمية مرتجعة لبند واحد على الأقل")

        totals = summarize(computed)
        prefix = "SRET" if original.kind == "sales" else "PRET"
        number = self.sequences.next_number(f"return_{original.kind}", prefix=prefix)

        ret = Invoice(
            company_id=self.company_id,
            kind="sales_return" if original.kind == "sales" else "purchase_return",
            number=number,
            contact_id=original.contact_id,
            issue_date=issue_date,
            due_date=None,
            status=status,
            notes=notes,
            currency=original.currency,
            subtotal=totals["subtotal"],
            discount_total=totals["discount_total"],
            tax_total=totals["tax_total"],
            total=totals["total"],
            created_by_id=self.user_id,
            issued_at=utcnow() if status == "issued" else None,
            return_of_id=original.id,
        )
        for entry in prepared:
            row = entry["row"]
            source_line = self.db.get(InvoiceLine, row["line_id"])
            ret.lines.append(
                InvoiceLine(
                    company_id=self.company_id,
                    description=row["description"],
                    quantity=entry["quantity"],
                    unit_price=row["unit_price"],
                    discount_rate=row["discount_rate"],
                    tax_rate=row["tax_rate"],
                    item_id=row["item_id"],
                    variant_id=source_line.variant_id if source_line else None,
                    warehouse_id=source_line.warehouse_id if source_line else None,
                    source_line_id=row["line_id"],
                )
            )
        self.invoices.add(ret)

        if status == "issued":
            self._move_return_stock(ret, original)
            self._post(ret)
        self.audit.record(
            action="invoice.return",
            entity_type="invoice",
            entity_id=ret.id,
            user_id=self.user_id,
            summary=f"مرتجع {ret.number} مقابل الفاتورة {original.number}",
        )
        self.db.flush()
        return ret

    def _move_return_stock(self, ret: Invoice, original: Invoice) -> None:
        """Undo the original stock move, at the cost frozen on the source line.

        A sales return puts the goods back at the exact cost they left with, so
        the margin reverses to the same figure it was booked at. A purchase
        return sends the goods back out, again at the original cost.
        """
        for line in ret.lines:
            if not line.item_id:
                continue
            source_line = self.db.get(InvoiceLine, line.source_line_id) if line.source_line_id else None
            item = self.inventory.items.get(line.item_id)
            if not item.track_inventory:
                continue
            warehouse_id = self.inventory.resolve_warehouse(item, line.warehouse_id)
            quantity = Decimal(str(line.quantity or 0))
            if quantity <= 0:
                continue
            unit_cost = ZERO
            if source_line and Decimal(str(source_line.quantity or 0)) > 0:
                if original.kind == "sales":
                    # Sales returns go back at the cost the goods left with.
                    unit_cost = Decimal(str(source_line.cost_amount)) / Decimal(str(source_line.quantity))
                else:
                    # A purchase return leaves at the price it was bought for.
                    unit_cost = Decimal(str(source_line.unit_price))

            if original.kind == "sales":
                self.inventory.receive(
                    item_id=item.id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    movement_date=ret.issue_date,
                    variant_id=line.variant_id,
                    kind="adjustment_in",
                    source_type="invoice_return",
                    source_id=ret.id,
                    reference=ret.number,
                    notes=f"مرتجع {original.number}",
                    post_journal=False,
                )
            else:
                self.inventory.issue(
                    item_id=item.id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    movement_date=ret.issue_date,
                    variant_id=line.variant_id,
                    kind="adjustment_out",
                    source_type="invoice_return",
                    source_id=ret.id,
                    reference=ret.number,
                    notes=f"مرتجع {original.number}",
                    post_journal=False,
                )
            # The cost that reverses on the return line, so the margin report
            # nets the return against the sale it belongs to. The origin split
            # is scaled from the source line by the fraction returned, so a
            # return of made goods reduces the manufactured bucket, not both.
            line.cost_amount = to_money(quantity * unit_cost)
            if source_line and Decimal(str(source_line.quantity or 0)) > 0:
                ratio = quantity / Decimal(str(source_line.quantity))
                line.cost_manufactured = to_money(Decimal(str(source_line.cost_manufactured)) * ratio)
                line.cost_purchased = to_money(Decimal(str(source_line.cost_purchased)) * ratio)
                line.quantity_manufactured = Decimal(str(source_line.quantity_manufactured)) * ratio
                line.quantity_purchased = Decimal(str(source_line.quantity_purchased)) * ratio
        self.db.flush()

    def get(self, invoice_id: int) -> Invoice:
        return self.invoices.get(invoice_id)

    def create(
        self,
        *,
        kind: str,
        contact_id: int,
        issue_date: date,
        due_date: date | None,
        lines: list[dict],
        notes: str = "",
        status: str = "issued",
    ) -> Invoice:
        if kind not in ("sales", "purchase"):
            raise ValidationError("Invoice kind must be sales or purchase")
        if not lines:
            raise ValidationError("Invoice needs at least one line")

        contact = self.contacts.get(contact_id)
        computed = [
            compute_line(
                line.get("quantity", 1),
                line.get("unit_price", 0),
                line.get("discount_rate", 0),
                line.get("tax_rate", 0),
            )
            for line in lines
        ]
        totals = summarize(computed)

        prefix = "INV" if kind == "sales" else "BILL"
        number = self.sequences.next_number(f"invoice_{kind}", prefix=prefix)

        invoice = Invoice(
            company_id=self.company_id,
            kind=kind,
            number=number,
            contact_id=contact.id,
            issue_date=issue_date,
            due_date=due_date,
            status=status,
            notes=notes,
            currency=contact_company_currency(contact, self.company_id),
            subtotal=totals["subtotal"],
            discount_total=totals["discount_total"],
            tax_total=totals["tax_total"],
            total=totals["total"],
            created_by_id=self.user_id,
            issued_at=utcnow() if status == "issued" else None,
        )
        for raw, _amounts in zip(lines, computed, strict=True):
            invoice.lines.append(
                InvoiceLine(
                    company_id=self.company_id,
                    description=raw.get("description", ""),
                    quantity=raw.get("quantity", 1),
                    unit_price=raw.get("unit_price", 0),
                    discount_rate=raw.get("discount_rate", 0),
                    tax_rate=raw.get("tax_rate", 0),
                    account_id=raw.get("account_id"),
                    item_id=raw.get("item_id"),
                    variant_id=raw.get("variant_id"),
                    warehouse_id=raw.get("warehouse_id"),
                )
            )
        self.invoices.add(invoice)

        if status == "issued":
            self._move_stock(invoice)
            self._post(invoice)
        self.audit.record(
            action="invoice.create",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=self.user_id,
            summary=f"{KIND_AR.get(kind, kind)} رقم {number} للجهة {contact.name}",
        )
        return invoice

    def update(
        self,
        invoice_id: int,
        *,
        kind: str | None = None,
        contact_id: int | None = None,
        issue_date: date | None = None,
        due_date: date | None = None,
        lines: list[dict] | None = None,
        notes: str | None = None,
    ) -> Invoice:
        """Edit an invoice, whether it is a draft or already issued.

        A draft has no accounting footprint, so it is rewritten in place. An
        issued invoice is an accounting fact: changing it means reversing its
        journal entry and its stock moves, then posting the new figures, so the
        books stay balanced and the audit trail keeps both versions. Editing is
        refused once money has been received against it or if its period is
        closed — those are corrected with a return or a reversal, not an edit.
        """
        invoice = self.invoices.get(invoice_id)
        if invoice.kind in RETURN_KINDS:
            raise ImmutableRecordError("لا يمكن تعديل مرتجع؛ ألغِه وأنشئ واحداً آخر")
        if invoice.status == "void":
            raise ImmutableRecordError("لا يمكن تعديل فاتورة ملغاة")
        if invoice.status != "draft" and to_money(invoice.amount_paid) > ZERO:
            raise ImmutableRecordError(
                "لا يمكن تعديل فاتورة عليها مدفوعات؛ اعمل مرتجعاً أو اعكس الدفعة أولاً"
            )

        if kind is not None and kind not in ("sales", "purchase"):
            raise ValidationError("Invoice kind must be sales or purchase")
        if lines is not None and not lines:
            raise ValidationError("Invoice needs at least one line")

        was_issued = invoice.status != "draft"
        if was_issued:
            # Undo the old fact before writing the new one.
            if invoice.journal_entry_id:
                self.accounting.reverse(invoice.journal_entry_id, reason=f"تعديل الفاتورة {invoice.number}")
                invoice.journal_entry_id = None
            self._reverse_stock(invoice, reason=f"تعديل الفاتورة {invoice.number}")
            invoice.status = "draft"

        if contact_id is not None:
            contact = self.contacts.get(contact_id)
            invoice.contact_id = contact.id
            invoice.currency = contact_company_currency(contact, self.company_id)
        if kind is not None:
            invoice.kind = kind
        if issue_date is not None:
            invoice.issue_date = issue_date
        if due_date is not None:
            invoice.due_date = due_date
        if notes is not None:
            invoice.notes = notes

        if lines is not None:
            computed = [
                compute_line(
                    line.get("quantity", 1),
                    line.get("unit_price", 0),
                    line.get("discount_rate", 0),
                    line.get("tax_rate", 0),
                )
                for line in lines
            ]
            totals = summarize(computed)
            invoice.subtotal = totals["subtotal"]
            invoice.discount_total = totals["discount_total"]
            invoice.tax_total = totals["tax_total"]
            invoice.total = totals["total"]
            invoice.lines.clear()
            self.db.flush()
            for raw in lines:
                invoice.lines.append(
                    InvoiceLine(
                        company_id=self.company_id,
                        description=raw.get("description", ""),
                        quantity=raw.get("quantity", 1),
                        unit_price=raw.get("unit_price", 0),
                        discount_rate=raw.get("discount_rate", 0),
                        tax_rate=raw.get("tax_rate", 0),
                        account_id=raw.get("account_id"),
                        item_id=raw.get("item_id"),
                        variant_id=raw.get("variant_id"),
                        warehouse_id=raw.get("warehouse_id"),
                    )
                )
        self.db.flush()

        if was_issued:
            self._reissue(invoice)

        self.audit.record(
            action="invoice.update",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=self.user_id,
            summary=f"تعديل {KIND_AR.get(invoice.kind, invoice.kind)} رقم {invoice.number}",
        )
        self.db.flush()
        return invoice

    def issue(self, invoice_id: int) -> Invoice:
        """Turn a draft into an accounting fact (post journal + move stock)."""
        invoice = self.invoices.get(invoice_id)
        if invoice.status != "draft":
            raise ValidationError("الفاتورة مُصدَرة بالفعل")
        self._reissue(invoice)
        self.audit.record(
            action="invoice.issue",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=self.user_id,
            summary=f"إصدار الفاتورة {invoice.number}",
        )
        self.db.flush()
        return invoice

    def _reissue(self, invoice: Invoice) -> None:
        invoice.status = "issued"
        invoice.issued_at = utcnow()
        self._move_stock(invoice)
        self._post(invoice)
        self.db.flush()

    def void(self, invoice_id: int, reason: str = "") -> Invoice:
        invoice = self.invoices.get(invoice_id)
        if invoice.status == "void":
            raise ImmutableRecordError("Invoice is already void")
        if to_money(invoice.amount_paid) > ZERO:
            raise ImmutableRecordError("Cannot void an invoice that has payments; reverse the payments first")
        # Voiding a return would leave the goods it moved unaccounted for, so it
        # is refused: a mistaken return is corrected by another return instead.
        if invoice.kind in RETURN_KINDS and invoice.status != "draft":
            raise ImmutableRecordError("لا يمكن إلغاء مرتجع مُصدَر؛ أنشئ مرتجعاً معاكساً")
        if invoice.journal_entry_id:
            self.accounting.reverse(invoice.journal_entry_id, reason=reason or f"void {invoice.number}")
        self._reverse_stock(invoice, reason)
        invoice.status = "void"
        self.audit.record(
            action="invoice.void",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=self.user_id,
            summary=f"إلغاء الفاتورة {invoice.number}: {reason}",
        )
        self.db.flush()
        return invoice

    def _reverse_stock(self, invoice: Invoice, reason: str) -> None:
        """Undo the stock a voided document moved, by posting the opposite move.

        A sale's issue is put back into the warehouse at the cost it left with,
        so the goods return at their original value rather than today's price.
        A purchase's receipt is taken back out, which is safe because voiding
        requires the invoice to be unpaid. A return's move is the mirror of the
        invoice it reverses, so voiding a return flips it once more.
        """
        is_return = invoice.kind in RETURN_KINDS
        # Voiding undoes the document's own stock move: a sales invoice issued
        # goods (void puts them back), a sales return brought them in (void takes
        # them out again), and the purchase kinds mirror that.
        issue_on_void = invoice.kind in ("purchase", "sales_return")
        for line in invoice.lines:
            if not line.item_id:
                continue
            item = self.inventory.items.get(line.item_id)
            if not item.track_inventory:
                continue
            warehouse_id = self.inventory.resolve_warehouse(item, line.warehouse_id)
            quantity = Decimal(str(line.quantity or 0))
            if quantity <= 0:
                continue

            if issue_on_void:
                self.inventory.issue(
                    item_id=item.id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    movement_date=invoice.issue_date,
                    variant_id=line.variant_id,
                    kind="adjustment_out",
                    source_type="invoice_void",
                    source_id=invoice.id,
                    reference=invoice.number,
                    notes=reason,
                    post_journal=False,
                )
            else:
                unit_cost = ZERO
                source_line = (
                    self.db.get(InvoiceLine, line.source_line_id) if line.source_line_id else None
                )
                if is_return and source_line and Decimal(str(source_line.quantity or 0)) > 0:
                    unit_cost = (
                        Decimal(str(source_line.cost_amount)) / Decimal(str(source_line.quantity))
                        if invoice.kind == "sales_return"
                        else Decimal(str(source_line.unit_price))
                    )
                else:
                    unit_cost = Decimal(str(line.cost_amount)) / quantity if quantity else ZERO
                self.inventory.receive(
                    item_id=item.id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    movement_date=invoice.issue_date,
                    variant_id=line.variant_id,
                    kind="adjustment_in",
                    source_type="invoice_void",
                    source_id=invoice.id,
                    reference=invoice.number,
                    notes=reason,
                    post_journal=False,
                )
        self.db.flush()

    def _move_stock(self, invoice: Invoice) -> None:
        """Move stock for every line that names an item, and freeze its cost.

        A sale issues goods and records what they cost. The split between
        manufactured and purchased comes from the layers the issue consumed,
        so a sale of mixed goods — some made, some bought — reports each part
        against its true cost without anyone labelling the invoice.
        """
        for line in invoice.lines:
            if not line.item_id:
                continue
            item = self.inventory.items.get(line.item_id)
            if not item.track_inventory:
                continue
            warehouse_id = self.inventory.resolve_warehouse(item, line.warehouse_id)
            quantity = Decimal(str(line.quantity or 0))
            if quantity <= 0:
                continue

            if invoice.kind == "sales":
                movement, cost = self.inventory.issue(
                    item_id=item.id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    movement_date=invoice.issue_date,
                    variant_id=line.variant_id,
                    kind="issue",
                    source_type="invoice",
                    source_id=invoice.id,
                    reference=invoice.number,
                    post_journal=False,
                )
                split = self.inventory.origin_split(movement.id)
                line.cost_amount = to_money(cost)
                line.cost_manufactured = split["manufactured"]["cost"]
                line.cost_purchased = split["purchased"]["cost"]
                line.quantity_manufactured = split["manufactured"]["quantity"]
                line.quantity_purchased = split["purchased"]["quantity"]
            else:
                unit_cost = Decimal(str(line.unit_price or 0))
                self.inventory.receive(
                    item_id=item.id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    movement_date=invoice.issue_date,
                    variant_id=line.variant_id,
                    kind="receipt",
                    source_type="invoice",
                    source_id=invoice.id,
                    reference=invoice.number,
                    post_journal=False,
                )
        self.db.flush()

    def _post(self, invoice: Invoice) -> None:
        # A return mirrors the document it reverses: same accounts, opposite
        # sides. Sales posts are reused with the sign flipped, which is what
        # keeps a return's figures cancelling the invoice's in every report.
        is_sales = invoice.kind in ("sales", "sales_return")
        is_return = invoice.kind in RETURN_KINDS

        control = self.accounts.get_by_code(AR_CONTROL_CODE if is_sales else AP_CONTROL_CODE)
        # Input and output VAT are netted on one account on purpose: the balance
        # is then what is actually due to the tax authority.
        vat = self.accounts.by_code(VAT_PAYABLE_CODE)
        default_account = self.accounts.by_code(SALES_CODE if is_sales else COGS_CODE)
        inventory = self.accounts.by_code(INVENTORY_CODE)
        cogs = self.accounts.by_code(COGS_CODE)

        lines: list[EntryLine] = []
        stock_cost = ZERO
        for line in invoice.lines:
            amounts = compute_line(line.quantity, line.unit_price, line.discount_rate, line.tax_rate)
            if amounts.net == ZERO:
                continue
            if is_sales:
                # A stocked line credits sales; its cost is posted separately
                # below so revenue and cost of sale stay on their own accounts.
                account_id = line.account_id or (default_account.id if default_account else control.id)
                if is_return:
                    lines.append(EntryLine(account_id=account_id, debit=amounts.net, description=line.description))
                else:
                    lines.append(EntryLine(account_id=account_id, credit=amounts.net, description=line.description))
                stock_cost += to_money(line.cost_amount)
            else:
                # A stocked purchase line debits inventory rather than an
                # expense: the cost belongs to the goods until they are sold.
                if line.item_id and inventory:
                    account_id = line.account_id or inventory.id
                else:
                    account_id = line.account_id or (default_account.id if default_account else control.id)
                if is_return:
                    lines.append(EntryLine(account_id=account_id, credit=amounts.net, description=line.description))
                else:
                    lines.append(EntryLine(account_id=account_id, debit=amounts.net, description=line.description))

        label = "مرتجع" if is_return else "فاتورة"
        if is_sales:
            if is_return:
                lines.append(
                    EntryLine(
                        account_id=control.id,
                        contact_id=invoice.contact_id,
                        credit=invoice.total,
                        description=f"{label} {invoice.number}",
                    )
                )
            else:
                lines.append(
                    EntryLine(
                        account_id=control.id,
                        contact_id=invoice.contact_id,
                        debit=invoice.total,
                        description=f"{label} {invoice.number}",
                    )
                )
            if to_money(invoice.tax_total) and vat:
                if is_return:
                    lines.append(EntryLine(account_id=vat.id, debit=invoice.tax_total, description="ضريبة القيمة المضافة"))
                else:
                    lines.append(EntryLine(account_id=vat.id, credit=invoice.tax_total, description="ضريبة القيمة المضافة"))
            if stock_cost > ZERO and cogs and inventory:
                if is_return:
                    lines.append(
                        EntryLine(account_id=cogs.id, credit=stock_cost, description=f"تكلفة مرتجع {invoice.number}")
                    )
                    lines.append(
                        EntryLine(account_id=inventory.id, debit=stock_cost, description=f"إعادة مخزون {invoice.number}")
                    )
                else:
                    lines.append(
                        EntryLine(account_id=cogs.id, debit=stock_cost, description=f"تكلفة مبيعات {invoice.number}")
                    )
                    lines.append(
                        EntryLine(account_id=inventory.id, credit=stock_cost, description=f"صرف مخزون {invoice.number}")
                    )
        else:
            if is_return:
                lines.append(
                    EntryLine(
                        account_id=control.id,
                        contact_id=invoice.contact_id,
                        debit=invoice.total,
                        description=f"{label} {invoice.number}",
                    )
                )
            else:
                lines.append(
                    EntryLine(
                        account_id=control.id,
                        contact_id=invoice.contact_id,
                        credit=invoice.total,
                        description=f"{label} {invoice.number}",
                    )
                )
            if to_money(invoice.tax_total) and vat:
                if is_return:
                    lines.append(EntryLine(account_id=vat.id, credit=invoice.tax_total, description="ضريبة القيمة المضافة"))
                else:
                    lines.append(EntryLine(account_id=vat.id, debit=invoice.tax_total, description="ضريبة القيمة المضافة"))

        memo_kind = {"sales": "Sales invoice", "purchase": "Purchase invoice", "sales_return": "Sales return", "purchase_return": "Purchase return"}[invoice.kind]
        entry = self.accounting.post(
            entry_date=invoice.issue_date,
            lines=lines,
            memo=f"{memo_kind} {invoice.number}",
            reference=invoice.number,
            source_type="invoice",
            source_id=invoice.id,
        )
        invoice.journal_entry_id = entry.id
        self.db.flush()


def contact_company_currency(contact, company_id: int) -> str:
    """Invoices inherit the company's base currency for now.

    Kept as a function so multi-currency can change the rule in one place.
    """
    return "EGP"
