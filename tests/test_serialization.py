"""Tests for shared JSON-safe serialization helpers."""

from __future__ import annotations

import datetime
import math
from decimal import Decimal

from angee.base.serialization import json_safe


def test_json_safe_normalizes_nested_values() -> None:
    """Nested scalar values are converted to deterministic JSON primitives."""

    value = {
        "date": datetime.date(2026, 5, 31),
        "time": datetime.time(12, 30, 15),
        "datetime": datetime.datetime(2026, 5, 31, 12, 0, 0),
        "decimal": Decimal("3.50"),
        "bytes": b"abc",
        "nonfinite": [math.nan, math.inf, -math.inf],
        "set": {"b", "a"},
        "frozen": frozenset((2, 1)),
        "items": (Decimal("1.25"), object()),
    }

    result = json_safe(value)

    assert result["date"] == "2026-05-31"
    assert result["time"] == "12:30:15"
    assert result["datetime"] == "2026-05-31T12:00:00"
    assert result["decimal"] == "3.50"
    assert result["bytes"] == "YWJj"
    assert result["nonfinite"] == [None, None, None]
    assert result["set"] == ["a", "b"]
    assert result["frozen"] == [1, 2]
    assert result["items"][0] == "1.25"
    assert isinstance(result["items"][1], str)
