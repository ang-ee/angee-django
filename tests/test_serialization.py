"""Tests for shared JSON-safe serialization helpers."""

from __future__ import annotations

import datetime
import hashlib
import math
from decimal import Decimal

import pytest

from angee.base.serialization import canonical_json, canonical_json_sha256, json_equal, json_safe


def test_canonical_json_digest_has_one_stable_unicode_spelling() -> None:
    """Key order, compact separators and UTF-8 hashing are shared facts."""

    canonical = '{"a":"é","b":2}'
    assert canonical_json({"b": 2, "a": "é"}) == canonical
    assert canonical_json_sha256({"b": 2, "a": "é"}) == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def test_canonical_json_rejects_nonfinite_values() -> None:
    """A digest never assigns a durable identity to non-standard JSON."""

    with pytest.raises(ValueError):
        canonical_json_sha256({"value": math.nan})


@pytest.mark.parametrize(
    ("left", "right", "equal"),
    [
        ([False], [0], False),
        (True, 1, False),
        ({"a": False}, {"a": 0}, False),
        ({"a": [True]}, {"a": [1.0]}, False),
        (None, False, False),
        ("1", 1, False),
        (1, 1.0, True),
        ({"a": [1]}, {"a": (1.0,)}, True),
        (1e16, 10**16, True),
        (-0.0, 0.0, True),
        (float(2**53 + 1), 2**53 + 1, False),
        ({"b": [1, 2], "a": "é"}, {"a": "é", "b": (1, 2)}, True),
        ([1, 2], [2, 1], False),
    ],
)
def test_json_equal_compares_json_values_never_python_ones(left: object, right: object, equal: bool) -> None:
    """A bool is never a number, and a number is the value its spelling denotes.

    ``1e+16`` and ``-0.0`` are what PostgreSQL's ``jsonb`` reads back as
    ``10000000000000000`` and ``0.0``; numbers compare exactly, so the float
    nearest ``2**53 + 1`` is another number.
    """

    assert json_equal(left, right) is equal
    assert json_equal(right, left) is equal


def test_json_equal_refuses_what_canonical_json_refuses() -> None:
    """A value without a JSON spelling has no JSON value to compare."""

    with pytest.raises(ValueError):
        json_equal(math.nan, math.nan)


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


def test_json_safe_preserves_escaped_unicode_set_order() -> None:
    """Set order remains compatible with the established JSON-safe projection."""

    assert json_safe({"z", "é"}) == ["é", "z"]
    assert sorted({"z", "é"}, key=canonical_json) == ["z", "é"]
