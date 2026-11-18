"""Pure accounting rules: money arithmetic and entry balance.

This module deliberately has no imports from the database or web layers, so the
rules can be reasoned about and property-tested in isolation. Anything that
needs I/O belongs in ``app.services``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..core.errors import UnbalancedEntryError, ValidationError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_money(value) -> Decimal:
    """Coerce anything numeric into a 2-decimal Decimal, half-up.

    Money must never be a float in this codebase: binary floats cannot
    represent 0.1 exactly, and rounding drift is unacceptable in accounting.
    """
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Invalid amount: {value!r}") from exc


def quantize_rate(value) -> Decimal:
    """Tax and discount rates keep four decimals (e.g. 14.0000)."""
    if value is None or value == "":
        return Decimal("0.0000")
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Invalid rate: {value!r}") from exc


@dataclass(frozen=True)
class LineAmounts:
    """Computed money values for one invoice line."""

    gross: Decimal
    discount: Decimal
    net: Decimal
    tax: Decimal
    total: Decimal


def compute_line(quantity, unit_price, discount_rate=0, tax_rate=0) -> LineAmounts:
    """Compute a line's amounts, rounding only at the money boundary.

    Intermediate values keep full precision so that a 3-line invoice taxed at
    14% sums to the same total as a single line of the same value.
    """
    qty = Decimal(str(quantity or 0))
    price = Decimal(str(unit_price or 0))
    discount_pct = quantize_rate(discount_rate) / Decimal("100")
    tax_pct = quantize_rate(tax_rate) / Decimal("100")

    gross = qty * price
    discount = gross * discount_pct
    net = gross - discount
    tax = net * tax_pct
    return LineAmounts(
        gross=to_money(gross),
        discount=to_money(discount),
        net=to_money(net),
        tax=to_money(tax),
        total=to_money(net + tax),
    )


def summarize(lines: list[LineAmounts]) -> dict[str, Decimal]:
    """Aggregate line amounts into invoice totals."""
    return {
        "subtotal": sum((line.net for line in lines), ZERO),
        "discount_total": sum((line.discount for line in lines), ZERO),
        "tax_total": sum((line.tax for line in lines), ZERO),
        "total": sum((line.total for line in lines), ZERO),
    }


@dataclass(frozen=True)
class EntryLine:
    account_id: int
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    contact_id: int | None = None
    description: str = ""


def validate_balanced(lines: list[EntryLine]) -> tuple[Decimal, Decimal]:
    """Assert debits equal credits; return the totals.

    Raises if there is no line, a line has both sides set, a line has neither
    side, or the two totals differ.
    """
    if not lines:
        raise ValidationError("Journal entry needs at least one line")

    debit_total = ZERO
    credit_total = ZERO
    for line in lines:
        debit = to_money(line.debit)
        credit = to_money(line.credit)
        if debit < 0 or credit < 0:
            raise ValidationError("Debit and credit must not be negative")
        if debit and credit:
            raise ValidationError("A journal line cannot have both debit and credit")
        if not debit and not credit:
            raise ValidationError("A journal line needs a debit or a credit")
        debit_total += debit
        credit_total += credit

    if debit_total != credit_total:
        raise UnbalancedEntryError(
            f"Entry is not balanced: debits {debit_total} != credits {credit_total}",
            details={"debits": str(debit_total), "credits": str(credit_total)},
        )
    if debit_total == ZERO:
        raise ValidationError("Journal entry has no value")

    return debit_total, credit_total


def reverse_lines(lines: list[EntryLine]) -> list[EntryLine]:
    """Swap debit and credit to build a reversing entry."""
    return [
        EntryLine(
            account_id=line.account_id,
            debit=to_money(line.credit),
            credit=to_money(line.debit),
            contact_id=line.contact_id,
            description=line.description,
        )
        for line in lines
    ]
