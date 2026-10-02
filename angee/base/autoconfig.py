"""Settings fragments required by Angee's model foundation."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES": [],
    "ANGEE_HOOKS": [],
    "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": True,
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    # Real folder hierarchies exceed the library's default depth of 8.
    "REBAC_DEPTH_LIMIT": 16,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # Admin reach is expressed in the schema (const-backed `admin` relations
    # -> angee/role:admin), so all actors use grants unless a host opts into the native bypass.
    "REBAC_SUPERUSER_BYPASS": False,
}
"""Django settings contributed when the model foundation is installed."""
