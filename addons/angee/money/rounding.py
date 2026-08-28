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
