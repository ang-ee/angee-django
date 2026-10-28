"""Settings owned by the Angee model and permission foundation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "COMPOSE_INTEGRATIONS:append": list[str],
        "ANGEE_MERGE_GUARDS": list[str],
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
    "COMPOSE_INTEGRATIONS:append": ["angee.base.build.PermissionIntegration"],
    # Additive guards refuse removal before one record is folded into another.
    "ANGEE_MERGE_GUARDS": [],
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # Administrator reach is expressed in schema relations, so every actor uses grants.
    "REBAC_SUPERUSER_BYPASS": False,
}


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
