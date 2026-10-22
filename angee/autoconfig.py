"""Settings fragments required by Angee's model foundation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_CHANGESET_MODEL": str,
        "ANGEE_IMPL_REGISTRIES": list[str],
        "ANGEE_HOOKS": list[str],
        "ANGEE_MERGE_GUARDS": list[str],
        "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": bool,
        "REBAC_BACKEND": str,
        "REBAC_LOCAL_BACKEND_STORAGE": str,
        "REBAC_STRICT_MODE": bool,
        "REBAC_LINT_BARE_PREFETCH": bool,
        "REBAC_FIELD_READ_MODE": str,
        "REBAC_ALLOW_SUDO": bool,
        "REBAC_SUPERUSER_BYPASS": bool,
    },
)

SETTINGS: _Settings = {
    "ANGEE_CHANGESET_MODEL": "provenance.Changeset",
    "ANGEE_IMPL_REGISTRIES": [],
    "ANGEE_HOOKS": ["ANGEE_MERGE_GUARDS"],
    # Dotted callables that refuse merging away one record (angee.models.fold_records).
    "ANGEE_MERGE_GUARDS": [],
    "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": True,
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # Admin reach is expressed in the schema (const-backed `admin` relations
    # -> angee/role:admin), so all actors use grants unless a host opts into the native bypass.
    "REBAC_SUPERUSER_BYPASS": False,
}
"""Django settings contributed when the model foundation is installed."""


class _DerivedSettings(TypedDict):
    REBAC_DEPTH_LIMIT: int


def settings(namespace: Mapping[str, Any]) -> _DerivedSettings:
    """Default permission depth to 8 on SQLite and 16 elsewhere.

    Autoconfig supplies environment values before project settings; either
    explicit value takes precedence over the backend default.
    """

    depth = namespace.get("REBAC_DEPTH_LIMIT")
    if depth is not None:
        return {"REBAC_DEPTH_LIMIT": int(depth)}
    engine = namespace.get("DATABASES", {}).get("default", {}).get("ENGINE")
    # Recursive permission SQL can exceed SQLite's expression-depth limit even
    # for shallow records; this bounds compilation and inherited permission hops.
    return {"REBAC_DEPTH_LIMIT": 8 if engine == "django.db.backends.sqlite3" else 16}
