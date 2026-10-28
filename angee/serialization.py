"""Canonical JSON serialization and JSON-safe coercion for runtime subsystems."""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

# These are native encoder profiles for existing persisted byte contracts.
# They intentionally differ from strict canonical_json; changing their options
# would rename resource hashes, import receipts and source payload identities.
ASCII_JSON = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=True,
)
"""Compact escaped JSON used by persisted seed hashes and unordered-value ordering."""

STRINGIFIED_JSON = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=True, default=str,
)
"""Compact Unicode JSON retaining native non-finite tokens and string fallbacks for import identities."""


ASCII_STRINGIFIED_JSON = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=True, default=str,
)
"""Escaped string-fallback JSON retained by domain hashes and payload normalization."""


def canonical_json(value: Any) -> str:
    """Serialize one JSON value with Angee's deterministic wire spelling."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_sha256(value: Any = None, *, chunks: Iterable[str] | None = None) -> str:
    """Hash a canonical value or a caller-owned deterministic JSON stream with one digest."""

    digest = hashlib.sha256()
    for chunk in chunks if chunks is not None else (canonical_json(value),):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def strip_null_bytes(value: Any) -> Any:
    """Remove NUL characters from strings and JSON keys before text/JSON storage."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {strip_null_bytes(key): strip_null_bytes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_null_bytes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_null_bytes(item) for item in value)
    return value


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

    return ASCII_JSON.encode(json_safe(value))
