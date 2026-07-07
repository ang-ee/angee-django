"""Decimal quantization with a mandatory, explicit rounding mode.

The framework's one home for "round this amount to N places by an explicitly named
mode." Unlike :meth:`decimal.Decimal.quantize`, whose ``rounding`` defaults to the
ambient decimal context, :func:`quantize` requires the mode as a positional
argument: a rounding *policy* (a currency exponent, a unit precision, the
``half_up`` / ``half_even`` tax rounding a company configures) is a decision its
owner must state, never a hidden context default. Callers map their own policy
enum to a ``decimal.ROUND_*`` constant and pass it here.
"""

from __future__ import annotations

from decimal import Decimal


def quantize(value: Decimal, places: int, mode: str) -> Decimal:
    """Return ``value`` rounded to ``places`` fractional digits using ``mode``.

    ``mode`` is a :mod:`decimal` rounding constant (``decimal.ROUND_HALF_UP``,
    ``decimal.ROUND_HALF_EVEN``, ...) and is required — there is no default, so a
    computation never rounds by an implicit context mode. ``places`` is the number
    of fractional digits to round to; ``0`` rounds to an integer.
    """

    exponent = Decimal(1).scaleb(-places)
    return value.quantize(exponent, rounding=mode)
