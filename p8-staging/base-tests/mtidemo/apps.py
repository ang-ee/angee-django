"""AppConfig for the REBAC-gated MTI grant demo (test-only installed app).

Registered in ``tests.settings`` so pytest-django creates the demo tables on
demand and ``rebac sync`` discovers the adjacent ``permissions.zed`` (its
``.path`` is this package). The app carries no ``addon.toml`` — it is a plain
Django app, not an Angee addon, so the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class MtiDemoConfig(AppConfig):
    """Installed app hosting the REBAC-gated MTI pair for the grant tests."""

    name = "tests.mtidemo"
    label = "mtidemo"
    default_auto_field = "django.db.models.BigAutoField"
