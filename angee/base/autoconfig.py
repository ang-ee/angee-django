"""Settings fragments required by Angee's model foundation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES": [],
    "ANGEE_HOOKS": [],
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


def settings(namespace: Mapping[str, Any]) -> dict[str, int]:
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
