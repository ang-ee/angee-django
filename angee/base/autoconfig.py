"""Settings fragments required by Angee's model foundation."""

from __future__ import annotations

SETTINGS = {
    "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": True,
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # The composed graph chains follower, thread, task, project, proposal, round,
    # roster and role reads: up to 11 permission frames. The library's default of
    # 8 stops short and silently falls back from SQL scopes to enumeration.
    "REBAC_DEPTH_LIMIT": 12,
    # Admin reach is expressed in the schema (const-backed `admin` relations
    # -> angee/role:admin), so all actors use grants unless a host opts into the native bypass.
    "REBAC_SUPERUSER_BYPASS": False,
    # Generic record links (bindings, attachments, needs) reach their targets
    # through content types, so the permission index tracks their writes.
    "REBAC_TRACKED_MODELS": ["contenttypes.ContentType"],
}
"""Django settings contributed when the model foundation is installed."""
