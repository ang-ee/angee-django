"""Canonical JSON serialization and JSON-safe coercion for runtime subsystems."""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize one JSON value with Angee's deterministic wire spelling."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 digest of :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``."""

    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, set | frozenset):
        return [json_safe(item) for item in sorted(value, key=_json_sort_key)]
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def _json_sort_key(value: Any) -> str:
    """Preserve escaped-Unicode ordering for unordered JSON-safe values.

    ``canonical_json`` emits Unicode directly, which would put ``"é"`` after
    ``"z"`` instead of before it and change the resulting array order.
    """

    return json.dumps(
        json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
    )
