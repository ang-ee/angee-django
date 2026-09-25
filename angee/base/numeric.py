"""Decimal quantization with a mandatory, explicit rounding mode.

The framework's one home for "round this amount to N places by an explicitly named
mode." Unlike :meth:`decimal.Decimal.quantize`, whose ``rounding`` defaults to the
ambient decimal context, :func:`quantize` requires the mode as a positional
argument: a rounding *policy* (a currency exponent, a unit precision, a
``half_up`` / ``half_even`` mode an owner configures) is a decision its
owner must state, never a hidden context default. Callers map their own policy
enum to a ``decimal.ROUND_*`` constant and pass it here.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

_INCREMENT_ROUNDING_MODES = frozenset((ROUND_HALF_UP, ROUND_HALF_EVEN))


def quantize(value: Decimal, places: int, mode: str) -> Decimal:
    """Return ``value`` rounded to ``places`` fractional digits using ``mode``.

    ``mode`` is a :mod:`decimal` rounding constant (``decimal.ROUND_HALF_UP``,
    ``decimal.ROUND_HALF_EVEN``, ...) and is required — there is no default, so a
    computation never rounds by an implicit context mode. ``places`` is the number
    of fractional digits to round to; ``0`` rounds to an integer.
    """

    exponent = Decimal(1).scaleb(-places)
    return value.quantize(exponent, rounding=mode)


def round_to_increment(value: Decimal, increment: Decimal, mode: str) -> Decimal:
    """Round ``value`` to an exact positive Decimal increment.

    Unlike exponent quantization, this supports increments such as ``0.05``.
    The calculation converts both finite decimals to integers at one common
    scale, rounds their exact quotient, and constructs the result directly from
    the increment's coefficient and exponent.  No intermediate Decimal division
    or ambient-context rounding can move a midpoint.

    Only the money owner's supported nearest rounding policies are accepted:
    :data:`decimal.ROUND_HALF_UP` and :data:`decimal.ROUND_HALF_EVEN`.
    """

    if not isinstance(value, Decimal) or not isinstance(increment, Decimal):
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

    def scaled_coefficient(decimal_value: Decimal) -> int:
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
    if doubled > denominator or (
        doubled == denominator
        and (mode == ROUND_HALF_UP or units % 2 == 1)
    ):
        units += 1
    if numerator < 0:
        units = -units

    increment_coefficient = int(
        "".join(str(digit) for digit in increment_tuple.digits)
    )
    result_coefficient = abs(units) * increment_coefficient
    result_digits = tuple(int(character) for character in str(result_coefficient))
    return Decimal(
        (
            1 if units < 0 else 0,
            result_digits,
            int(increment_tuple.exponent),
        )
    )
