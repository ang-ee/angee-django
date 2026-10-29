"""Exact money increment rounding preserves ties and input boundaries."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest

from angee.money.rounding import round_to_increment


def test_round_to_increment_preserves_signed_half_up_ties() -> None:
    """A non-power-of-ten midpoint rounds away from zero without division drift."""

    assert round_to_increment(
        Decimal("1.025"), Decimal("0.05"), ROUND_HALF_UP
    ) == Decimal("1.05")
    assert round_to_increment(
        Decimal("-1.025"), Decimal("0.05"), ROUND_HALF_UP
    ) == Decimal("-1.05")


def test_round_to_increment_uses_even_increment_units_at_ties() -> None:
    """HALF_EVEN chooses the even multiple, not the even decimal coefficient."""

    assert round_to_increment(
        Decimal("1.025"), Decimal("0.05"), ROUND_HALF_EVEN
    ) == Decimal("1.00")
    assert round_to_increment(
        Decimal("1.075"), Decimal("0.05"), ROUND_HALF_EVEN
    ) == Decimal("1.10")


def test_round_to_increment_supports_non_fractional_and_non_decimal_quantums() -> None:
    """The owner accepts every exact positive Decimal increment, not only exponents."""

    assert round_to_increment(
        Decimal("12"), Decimal("5"), ROUND_HALF_UP
    ) == Decimal("10")
    assert round_to_increment(
        Decimal("1.13"), Decimal("0.25"), ROUND_HALF_UP
    ) == Decimal("1.25")


@pytest.mark.parametrize(
    ("value", "increment", "mode", "error"),
    [
        (Decimal("NaN"), Decimal("0.05"), ROUND_HALF_UP, ValueError),
        (Decimal("1"), Decimal("Infinity"), ROUND_HALF_UP, ValueError),
        (Decimal("1"), Decimal("0"), ROUND_HALF_UP, ValueError),
        (Decimal("1"), Decimal("-0.05"), ROUND_HALF_UP, ValueError),
        (Decimal("1"), Decimal("0.05"), ROUND_DOWN, ValueError),
        (1, Decimal("0.05"), ROUND_HALF_UP, TypeError),
    ],
)
def test_round_to_increment_rejects_ambiguous_inputs(
    value: object,
    increment: Decimal,
    mode: str,
    error: type[Exception],
) -> None:
    """Invalid values, increments, and policies fail before arithmetic."""

    with pytest.raises(error):
        round_to_increment(value, increment, mode)  # type: ignore[arg-type]
