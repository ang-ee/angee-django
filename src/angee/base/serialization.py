"""Canonical JSON serialization, JSON value comparison and bounds, and JSON-safe coercion for runtime subsystems."""

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


MAX_JSON_DEPTH = 64
"""How many arrays and objects may enclose a member of a JSON value Angee accepts.

The deepest values addons accept sit well inside it: a dashboard snapshot
whose widget filter uses all ten of its logical levels nests 28, a web form
definition with panels at their three-deep cap 14. The bound keeps the
recursive walks an accepted value meets (strawberry-django-hasura's input
reduction, Django's ``JSONField``, JSON Schema validation) far inside Python's
recursion limit.
"""

MAX_JSON_MEMBERS = 1_000_000
"""How many members a JSON value Angee accepts may hold, as an expanding walk counts them.

A member is an object's value or an array's item at any level, and a container
reached through several references counts once for each. The largest value an
addon's own caps admit, a web form definition of 200 questions with 200
two-language choices each, holds about 200,000 members and fills a request
body; a tree in Django's default 2.5 MiB body holds at most 1,310,720. So the
bound refuses a tree only when it is a near-body-sized flat array, and stops
shared references from multiplying the work of the walks that expand a value
later.
"""

_JSON_CONTAINERS = (dict, list, tuple)


def json_bound_violation(
    value: Any, *, depth: int = MAX_JSON_DEPTH, members: int | None = MAX_JSON_MEMBERS,
) -> str | None:
    """Return how ``value`` exceeds a JSON bound, as a phrase, or ``None`` within them.

    ``depth`` bounds how many arrays and objects may enclose a member, an
    object's value or an array's item. ``members``, unless ``None``, bounds how
    many members the value holds as an expanding walk meets them: a container
    reached through several references counts once for each. Tuples are arrays.
    An explicit stack measures each distinct container once, so neither nesting
    nor shared references make the walk recurse or repeat its work.
    """

    if not isinstance(value, _JSON_CONTAINERS):
        return None
    deeper = f"nests deeper than {depth} levels"
    larger = f"holds more than {members} members"
    finished: dict[int, tuple[int, int]] = {}
    total = 0

    def opened(container: Any, level: int) -> list[Any] | str:
        """Count a container's members, which sit at ``level``, and frame its nested containers."""

        nonlocal total
        held = container.values() if isinstance(container, dict) else container
        total += len(held)
        if held and level > depth:
            return deeper
        if members is not None and total > members:
            return larger
        # A frame is [container, its unread nested containers, height, members held at any level].
        return [container, iter([item for item in held if isinstance(item, _JSON_CONTAINERS)]), 0, len(held)]

    root = opened(value, 1)
    if isinstance(root, str):
        return root
    frames = [root]
    while frames:
        frame = frames[-1]
        for child in frame[1]:
            measured = finished.get(id(child))
            if measured is None:
                entered = opened(child, len(frames) + 1)
                if isinstance(entered, str):
                    return entered
                frames.append(entered)
                break
            height, held = measured
            total += held
            if len(frames) + height > depth:
                return deeper
            if members is not None and total > members:
                return larger
            frame[2] = max(frame[2], height + 1)
            frame[3] += held
        else:
            frames.pop()
            height = frame[2] or (1 if frame[3] else 0)
            finished[id(frame[0])] = (height, frame[3])
            if frames:
                frames[-1][2] = max(frames[-1][2], height + 1)
                frames[-1][3] += frame[3]
    return None


def json_equal(left: Any, right: Any) -> bool:
    """Return whether two values are one JSON value, as JSON Schema and PostgreSQL ``jsonb`` compare them.

    A bool or ``null`` equals only itself, never a number, although Python's
    ``False == 0`` holds. A number is the value its spelling denotes, so ``1`` and
    ``1.0`` are one number, as are the ``1e+16`` a projection writes and the
    ``10000000000000000`` a ``jsonb`` column reads back. Arrays compare in order,
    however Python spells them, and objects compare by key. Both values serialize
    through :func:`canonical_json`, which refuses a non-JSON value.
    """

    def value(item: Any) -> Any:
        return json.loads(canonical_json(item), parse_int=_json_number, parse_float=_json_number)

    return bool(value(left) == value(right))


def _json_number(spelling: str) -> tuple[str, Decimal]:
    """Tag a JSON number with the exact decimal it spells, so it equals only a number of that value."""

    return ("number", Decimal(spelling))


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
