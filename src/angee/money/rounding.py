"""Money-owned rounding vocabulary."""

from __future__ import annotations

import decimal
from types import MappingProxyType
from typing import Mapping

from django.db import models


class RoundingMode(models.TextChoices):
    """Named rounding policies supported by money calculations."""

    HALF_UP = "half_up", "Half Up"
    HALF_EVEN = "half_even", "Half Even"


ROUNDING_CONSTANTS: Mapping[str, str] = MappingProxyType(
    {
        "half_up": decimal.ROUND_HALF_UP,
        "half_even": decimal.ROUND_HALF_EVEN,
    }
)
"""``RoundingMode`` value -> :mod:`decimal` rounding constant."""

DEFAULT_ROUNDING_MODE = "half_up"
"""Default money rounding policy when a caller does not override it."""


def rounding_constant(mode: RoundingMode | str | None = None) -> str:
    """Return the :mod:`decimal` rounding constant for one money rounding mode."""

    value = DEFAULT_ROUNDING_MODE if mode is None else getattr(mode, "value", mode)
    text = str(value)
    if text in ROUNDING_CONSTANTS:
        return ROUNDING_CONSTANTS[text]
    raise ValueError(f"Unknown money rounding mode {value!r}.")


_INCREMENT_ROUNDING_MODES = frozenset((decimal.ROUND_HALF_UP, decimal.ROUND_HALF_EVEN))


def round_to_increment(value: decimal.Decimal, increment: decimal.Decimal, mode: str) -> decimal.Decimal:
    """Round ``value`` to an exact positive Decimal increment.

    Unlike exponent quantization, this supports increments such as ``0.05``.
    The calculation converts both finite decimals to integers at one common
    scale, rounds their exact quotient, and constructs the result directly from
    the increment's coefficient and exponent.  No intermediate decimal.Decimal division
    or ambient-context rounding can move a midpoint.

    Only the money owner's supported nearest rounding policies are accepted:
    :data:`decimal.ROUND_HALF_UP` and :data:`decimal.ROUND_HALF_EVEN`.
    """

    if not isinstance(value, decimal.Decimal) or not isinstance(increment, decimal.Decimal):
        raise TypeError("Increment rounding requires Decimal values.")
    if not value.is_finite():
        raise ValueError("Increment rounding requires a finite value.")
    if not increment.is_finite() or increment <= 0:
        raise ValueError("Increment rounding requires a finite positive increment.")
    if mode not in _INCREMENT_ROUNDING_MODES:
        raise ValueError(f"Unsupported increment rounding mode {mode!r}.")

    value_tuple = value.as_tuple()
    increment_tuple = increment.as_tuple()
    common_exponent = min(
        int(value_tuple.exponent),
        int(increment_tuple.exponent),
    )

    def scaled_coefficient(decimal_value: decimal.Decimal) -> int:
        decimal_tuple = decimal_value.as_tuple()
        digits = decimal_tuple.digits
        coefficient = int("".join(str(digit) for digit in digits))
        exponent = int(decimal_tuple.exponent)
        sign = -1 if decimal_tuple.sign else 1
        return sign * coefficient * (10 ** (exponent - common_exponent))

    numerator = scaled_coefficient(value)
    denominator = scaled_coefficient(increment)
    units, remainder = divmod(abs(numerator), denominator)
    doubled = remainder * 2
    if doubled > denominator or (doubled == denominator and (mode == decimal.ROUND_HALF_UP or units % 2 == 1)):
        units += 1
    if numerator < 0:
        units = -units

    increment_coefficient = int("".join(str(digit) for digit in increment_tuple.digits))
    result_coefficient = abs(units) * increment_coefficient
    result_digits = tuple(int(character) for character in str(result_coefficient))
    return decimal.Decimal(
        (
            1 if units < 0 else 0,
            result_digits,
            int(increment_tuple.exponent),
        )
    )
