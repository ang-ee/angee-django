"""Tests for the explicit-mode decimal quantize helper."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest

from angee.base.numeric import quantize


def test_quantize_half_up_rounds_half_away_from_zero() -> None:
    """HALF_UP rounds a trailing 5 up regardless of the preceding digit."""

    assert quantize(Decimal("2.345"), 2, ROUND_HALF_UP) == Decimal("2.35")
    assert quantize(Decimal("2.5"), 0, ROUND_HALF_UP) == Decimal("3")


def test_quantize_half_even_rounds_to_even() -> None:
    """HALF_EVEN (banker's rounding) rounds a trailing 5 to the nearest even digit."""

    assert quantize(Decimal("2.345"), 2, ROUND_HALF_EVEN) == Decimal("2.34")
    assert quantize(Decimal("2.5"), 0, ROUND_HALF_EVEN) == Decimal("2")


def test_quantize_places_zero_rounds_to_integer() -> None:
    """``places=0`` quantizes to an integer exponent."""

    assert quantize(Decimal("1.4"), 0, ROUND_HALF_UP) == Decimal("1")


def test_quantize_requires_an_explicit_mode() -> None:
    """The rounding mode is a required argument — no hidden default rounding."""

    with pytest.raises(TypeError):
        quantize(Decimal("1.5"), 0)  # type: ignore[call-arg]
