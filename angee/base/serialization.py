"""JSON-safe value coercion shared by Angee runtime subsystems."""

from __future__ import annotations

import base64
import datetime
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


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
    """Return a deterministic ordering key for unordered JSON-safe values."""

    return json.dumps(
        json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
    )
