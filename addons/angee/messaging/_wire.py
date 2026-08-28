"""Console-safe coercions shared by messaging vendor wire adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


def text(value: object) -> str:
    """Return one wire scalar as stripped text."""

    return str(value or "").strip()


def mapping(value: object) -> Mapping[str, Any] | None:
    """Return ``value`` when it is a mapping."""

    return value if isinstance(value, Mapping) else None


def sequence(value: object) -> Sequence[Any]:
    """Return a non-text wire sequence, or an empty tuple."""

    return value if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray) else ()


def millis_to_utc(value: object) -> datetime | None:
    """Convert a positive millisecond epoch to an aware UTC instant."""

    try:
        milliseconds = int(str(value))
    except (TypeError, ValueError):  # fmt: skip
        return None
    if milliseconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):  # fmt: skip
        return None
