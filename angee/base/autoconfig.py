"""Settings fragments required by Angee's model foundation."""

from __future__ import annotations

SETTINGS = {
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # Admin reach is expressed in the schema (const-backed `admin` relations
    # -> angee/role:admin), so superusers go through REBAC like everyone else.
    "REBAC_SUPERUSER_BYPASS": False,
}
"""Django settings contributed when the model foundation is installed."""
