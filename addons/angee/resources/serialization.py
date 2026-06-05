"""JSON-safe value coercion shared by runtime and resource code."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


def json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)
