"""Deterministic, tagged encodings for lossless native field values."""

from __future__ import annotations

import base64
import hashlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from angee.base.serialization import canonical_json as canonical_json


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Lossless JSON mappings require string keys.")
        encoded = {key: json_safe(item) for key, item in sorted(value.items())}
        # Escape user data resembling a tagged native value.
        return {"$type": "mapping", "value": encoded} if "$type" in value else encoded
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return {"$type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, timedelta):
        return {
            "$type": "timedelta",
            "days": value.days,
            "seconds": value.seconds,
            "microseconds": value.microseconds,
        }
    if isinstance(value, (Decimal, UUID)):
        return {"$type": type(value).__name__, "value": str(value)}
    if isinstance(value, (bytes, memoryview)):
        return {"$type": "bytes", "value": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError(f"No lossless encoding for {type(value).__name__}.")


def creation_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(json_safe(value)).encode()).hexdigest()


def restore_json(value: Any) -> Any:
    """Decode values produced by json_safe, including escaped JSON mappings."""
    if isinstance(value, list):
        return [restore_json(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("$type")
    if kind == "mapping":
        return {key: restore_json(item) for key, item in value["value"].items()}
    if kind == "datetime":
        return datetime.fromisoformat(value["value"])
    if kind == "date":
        return date.fromisoformat(value["value"])
    if kind == "time":
        return time.fromisoformat(value["value"])
    if kind == "timedelta":
        return timedelta(days=value["days"], seconds=value["seconds"], microseconds=value["microseconds"])
    if kind == "Decimal":
        return Decimal(value["value"])
    if kind == "UUID":
        return UUID(value["value"])
    if kind == "bytes":
        return base64.b64decode(value["value"])
    return {key: restore_json(item) for key, item in value.items()}
