"""Manual REBAC schema setup for tests that bypass composed-host sync."""

from __future__ import annotations

from django.core.management import call_command
from rebac.backends import LocalBackend, backend
from rebac.schema import Schema


def install_manual_schema(schema: Schema, *, active: LocalBackend | None = None) -> LocalBackend:
    """Make a manual local schema current and rebuild its shared permission index."""

    import rebac.backends as backends

    installed = active or backend()
    if not isinstance(installed, LocalBackend):
        raise TypeError("Manual schema tests require the local REBAC backend")
    backends._backend = installed
    installed.set_schema(schema)
    call_command("rebac", "index", "rebuild", verbosity=0)
    return installed
