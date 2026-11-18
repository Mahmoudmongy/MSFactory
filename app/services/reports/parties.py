"""Reports about the people a company deals with: customers and suppliers.

Aging is the one every business runs first, because it says who owes money and
how late it is. Statements answer the follow-up question — "why is this
balance what it is?" — and the balance-per-party reports are the collections
and payments worklists.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ...db.models import Contact, JournalLine
from .base import ZERO, money

#: The aging bands every receivables report is cut into.
AGING_BUCKETS = ("current", "1_30", "31_60", "61_90", "90_plus")


class PartiesMixin:
    """Receivables, payables, aging, statements and party balances."""

    def _aging(self, kind: str, upto: date | None = None) -> dict:
        """Shared aging engine for sales (receivables) and purchase (payables).

        Buckets are measured from the issue date rather than the due date, so
        the report answers "how old is this debt" independently of the terms
        that were agreed. Credit terms are shown per party separately.
        """
        upto = upto or datetime.now(UTC).date()
        buckets = dict.fromkeys(AGING_BUCKETS, ZERO)
        per_contact: dict[int, dict] = {}

        for invoice in self.invoices.outstanding_invoices(kind):
            outstanding = money(invoice.total) - money(invoice.amount_paid)
            if outstanding <= ZERO:
                continue
            days = (upto - invoice.issue_date).days
            key = (
                "current"
                if days <= 0
                else "1_30"
                if days <= 30
                else "31_60"
                if days <= 60
                else "61_90"
                if days <= 90
                else "90_plus"
            )
            buckets[key] += outstanding
            row = per_contact.setdefault(
                invoice.contact_id,
                {
                    "contact_id": invoice.contact_id,
                    "name": invoice.contact.name if invoice.contact else "",
                    "phone": invoice.contact.phone if invoice.contact else "",
                    "credit_limit": money(invoice.contact.credit_limit) if invoice.contact else ZERO,
                    "payment_terms_days": invoice.contact.payment_terms_days if invoice.contact else 0,
                    **dict.fromkeys(AGING_BUCKETS, ZERO),
                    "total": ZERO,
                },
            )
            row[key] += outstanding
            row["total"] += outstanding

        contacts = sorted(per_contact.values(), key=lambda r: r["total"], reverse=True)
        # A limit only matters if it is set; zero means "no limit recorded".
        over_limit = [
            {"contact_id": c["contact_id"], "name": c["name"], "total": c["total"], "limit": c["credit_limit"]}
            for c in contacts
            if c["credit_limit"] > ZERO and c["total"] > c["credit_limit"]
        ]
        return {
            "buckets": buckets,
            "contacts": contacts,
            "total": sum((c["total"] for c in contacts), ZERO),
            "over_limit": over_limit,
        }

    def ar_aging(self, upto: date | None = None) -> dict:
        """Who owes the company, and how overdue."""
        return self._aging("sales", upto)

    def ap_aging(self, upto: date | None = None) -> dict:
        """Whom the company owes, and how overdue."""
        return self._aging("purchase", upto)

    def customer_balances(self, upto: date | None = None) -> dict:
        """Every customer with what they owe, their limit and their oldest debt."""
        return self._party_balances("customer", upto)

    def supplier_balances(self, upto: date | None = None) -> dict:
        """Every supplier with what the company owes them."""
        return self._party_balances("supplier", upto)

    def _party_balances(self, kind: str, upto: date | None) -> dict:
        """Balances from the journal, so the figure ties to the control account.

        Reading the contact's journal balance rather than summing invoices
        means a manual entry, a credit note or a payment on account is included
        — anything that actually moved the control account.
        """
        contacts = self.contacts.list(kind=kind)
        rows = []
        for contact in contacts:
            balance = self._contact_balance(contact.id, upto)
            if balance == ZERO:
                continue
            rows.append(
                {
                    "contact_id": contact.id,
                    "name": contact.name,
                    "phone": contact.phone,
                    "email": contact.email,
                    "credit_limit": money(contact.credit_limit),
                    "payment_terms_days": contact.payment_terms_days,
                    "balance": balance,
                }
            )
        rows.sort(key=lambda r: r["balance"], reverse=True)
        return {
            "contacts": rows,
            "total": sum((r["balance"] for r in rows), ZERO),
            "count": len(rows),
        }

    def _contact_balance(self, contact_id: int, upto: date | None = None) -> Decimal:
        """A party's balance as the journal sees it, debit-minus-credit."""
        stmt = select(
            func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0)
        ).where(JournalLine.company_id == self.company_id, JournalLine.contact_id == contact_id)
        if upto:
            stmt = stmt.where(JournalLine.entry_id.in_(self._entry_ids(None, upto)))
        return money(self.db.scalar(stmt))

    def contact_statement(
        self, contact_id: int, start: date | None = None, end: date | None = None
    ) -> dict:
        """One party's full history with a running balance and the open documents.

        The running balance starts from everything before the window, so the
        closing figure is the real balance rather than the period's movement.
        """
        contact = self.contacts.get(contact_id)
        opening = self._contact_balance(contact_id, start - timedelta(days=1)) if start else ZERO

        stmt = (
            select(JournalLine)
            .where(
                JournalLine.company_id == self.company_id,
                JournalLine.contact_id == contact_id,
                JournalLine.entry_id.in_(self._entry_ids(start, end)),
            )
            .order_by(JournalLine.entry_id, JournalLine.id)
        )

        running = opening
        movements = []
        for line in self.db.scalars(stmt):
            debit, credit = money(line.debit), money(line.credit)
            running += debit - credit
            movements.append(
                {
                    "id": line.id,
                    "entry_id": line.entry_id,
                    "date": line.entry.entry_date if line.entry else None,
                    "reference": line.entry.reference if line.entry else "",
                    "description": line.description,
                    "account_name": (line.account.name_ar or line.account.name) if line.account else "",
                    "debit": debit,
                    "credit": credit,
                    "balance": running,
                }
            )

        invoices = self.invoices.list(contact_id=contact_id)
        open_invoices = [
            {
                "id": i.id,
                "number": i.number,
                "kind": i.kind,
                "issue_date": i.issue_date,
                "due_date": i.due_date,
                "total": money(i.total),
                "paid": money(i.amount_paid),
                "outstanding": money(i.total) - money(i.amount_paid),
                "status": i.status,
            }
            for i in invoices
            if i.status in ("issued", "partially_paid")
        ]

        return {
            "contact": {
                "id": contact.id,
                "name": contact.name,
                "kind": contact.kind,
                "phone": contact.phone,
                "email": contact.email,
                "address": contact.address,
                "tax_number": contact.tax_number,
                "credit_limit": money(contact.credit_limit),
                "payment_terms_days": contact.payment_terms_days,
            },
            "opening": opening,
            "movements": movements,
            "closing": running,
            "balance": self._contact_balance(contact_id),
            "open_invoices": open_invoices,
            "total_due": sum((i["outstanding"] for i in open_invoices), ZERO),
        }

    def top_customers(self, start: date | None = None, end: date | None = None, limit: int = 20) -> dict:
        """Customers ranked by the profit they brought in, not by turnover.

        Revenue alone flatters a customer who is expensive to serve. Profit per
        customer is taken from each sold line's frozen cost, so it is the margin
        actually earned rather than today's price list applied to old sales.

        Built from the invoices rather than the journal because the journal
        carries the customer only on the control line, not on the revenue line,
        so a journal join would attribute every sale to no one.
        """
        from ...db.models import Invoice, InvoiceLine
        from ...domain.money import compute_line

        stmt = (
            select(Invoice.contact_id, Contact.name, InvoiceLine)
            .join(Contact, Contact.id == Invoice.contact_id)
            .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
            .where(
                Contact.company_id == self.company_id,
                Contact.kind == "customer",
                Invoice.company_id == self.company_id,
                Invoice.kind == "sales",
                Invoice.status != "void",
            )
        )
        if start:
            stmt = stmt.where(Invoice.issue_date >= start)
        if end:
            stmt = stmt.where(Invoice.issue_date <= end)

        per_customer: dict[int, dict] = {}
        for contact_id, name, line in self.db.execute(stmt):
            amounts = compute_line(line.quantity, line.unit_price, line.discount_rate, line.tax_rate)
            row = per_customer.setdefault(
                contact_id,
                {"contact_id": contact_id, "name": name, "revenue": ZERO, "cost": ZERO, "invoice_count": 0},
            )
            row["revenue"] += amounts.net
            row["cost"] += money(line.cost_amount)

        customers = []
        for row in per_customer.values():
            row["profit"] = money(row["revenue"] - row["cost"])
            row["margin_percent"] = (
                (row["profit"] / row["revenue"] * 100).quantize(Decimal("0.01")) if row["revenue"] else ZERO
            )
            customers.append(row)
        customers.sort(key=lambda r: r["profit"], reverse=True)
        customers = customers[:limit]

        total = sum((c["profit"] for c in customers), ZERO)
        for customer in customers:
            customer["share_percent"] = (
                (customer["profit"] / total * 100).quantize(Decimal("0.01")) if total else ZERO
            )
        return {"customers": customers, "total": total, "count": len(customers)}
