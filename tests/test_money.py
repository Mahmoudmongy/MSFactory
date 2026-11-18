"""Domain tests: money arithmetic and balance validation.

These are pure functions, so they run without a database and are the fastest
safety net for the most critical rules.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from app.core.errors import UnbalancedEntryError, ValidationError
from app.domain.money import (
    EntryLine,
    compute_line,
    reverse_lines,
    summarize,
    to_money,
    validate_balanced,
)


class TestMoney:
    def test_quantizes_to_two_decimals_half_up(self):
        assert to_money("1.005") == Decimal("1.01")
        assert to_money("1.004") == Decimal("1.00")
        assert to_money(2.5) == Decimal("2.50")

    def test_treats_none_and_empty_as_zero(self):
        assert to_money(None) == Decimal("0.00")
        assert to_money("") == Decimal("0.00")

    def test_rejects_garbage(self):
        with pytest.raises(ValidationError):
            to_money("not-a-number")

    @given(
        st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100000"), places=2),
        st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000"), places=2),
    )
    @hyp_settings(max_examples=200)
    def test_quantity_times_price_is_commutative(self, qty, price):
        assert compute_line(qty, price).gross == compute_line(price, qty).gross


class TestLineComputation:
    def test_applies_discount_then_tax(self):
        amounts = compute_line(quantity=2, unit_price=1000, discount_rate=10, tax_rate=14)
        assert amounts.gross == Decimal("2000.00")
        assert amounts.discount == Decimal("200.00")
        assert amounts.net == Decimal("1800.00")
        assert amounts.tax == Decimal("252.00")
        assert amounts.total == Decimal("2052.00")

    def test_zero_rates_pass_through(self):
        amounts = compute_line(1, 100)
        assert amounts.net == Decimal("100.00")
        assert amounts.tax == Decimal("0.00")
        assert amounts.total == Decimal("100.00")

    @given(
        st.lists(
            st.tuples(
                st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2),
                st.decimals(min_value=Decimal("1"), max_value=Decimal("1000"), places=2),
            ),
            min_size=1,
            max_size=6,
        )
    )
    @hyp_settings(max_examples=150)
    def test_invoice_total_equals_sum_of_line_totals(self, raw_lines):
        """Property: the invoice total is exactly the sum of its line totals."""
        lines = [compute_line(q, p, tax_rate=14) for q, p in raw_lines]
        totals = summarize(lines)
        assert totals["total"] == sum(line.total for line in lines)


class TestBalanceValidation:
    def test_accepts_balanced_entry(self):
        debit, credit = validate_balanced(
            [EntryLine(account_id=1, debit=Decimal("100")), EntryLine(account_id=2, credit=Decimal("100"))]
        )
        assert debit == credit == Decimal("100.00")

    def test_rejects_unbalanced_entry(self):
        with pytest.raises(UnbalancedEntryError) as exc:
            validate_balanced(
                [EntryLine(account_id=1, debit=Decimal("100")), EntryLine(account_id=2, credit=Decimal("50"))]
            )
        assert "not balanced" in str(exc.value).lower()

    def test_rejects_empty_entry(self):
        with pytest.raises(ValidationError):
            validate_balanced([])

    def test_rejects_line_with_both_sides(self):
        with pytest.raises(ValidationError):
            validate_balanced(
                [
                    EntryLine(account_id=1, debit=Decimal("10"), credit=Decimal("10")),
                    EntryLine(account_id=2, debit=Decimal("10")),
                ]
            )

    def test_rejects_line_with_no_amount(self):
        with pytest.raises(ValidationError):
            validate_balanced(
                [EntryLine(account_id=1), EntryLine(account_id=2, credit=Decimal("10"))]
            )

    def test_rejects_negative_amounts(self):
        with pytest.raises(ValidationError):
            validate_balanced(
                [EntryLine(account_id=1, debit=Decimal("-10")), EntryLine(account_id=2, credit=Decimal("-10"))]
            )

    def test_rejects_zero_value_entry(self):
        with pytest.raises(ValidationError):
            validate_balanced([EntryLine(account_id=1), EntryLine(account_id=2)])

    @given(
        st.lists(
            st.decimals(min_value=Decimal("0.01"), max_value=Decimal("10000"), places=2),
            min_size=1,
            max_size=8,
        )
    )
    @hyp_settings(max_examples=200)
    def test_reversal_always_balances(self, amounts):
        """Property: mirroring a balanced entry yields a balanced entry."""
        lines = []
        for index, amount in enumerate(amounts):
            lines.append(EntryLine(account_id=index, debit=amount))
        total = sum(amounts, Decimal("0"))
        lines.append(EntryLine(account_id=999, credit=total))
        validate_balanced(lines)  # sanity: the constructed entry is valid

        mirror = reverse_lines(lines)
        debit, credit = validate_balanced(mirror)
        assert debit == credit
        # Mirroring twice returns the original amounts.
        assert [(ln.debit, ln.credit) for ln in reverse_lines(mirror)] == [
            (ln.debit, ln.credit) for ln in lines
        ]
