"""AppConfig for the record-chatter REBAC demo (test-only installed app).

Registered in ``tests.settings`` so pytest-django can build the demo table on
demand and ``rebac sync`` discovers the adjacent ``permissions.zed`` (its
``.path`` is this package). The app carries no ``addon.toml`` — it is a plain
Django app, not an Angee addon, so the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class ChatterDemoConfig(AppConfig):
    """Installed app hosting the gated threaded record used by the messaging tests."""

    name = "tests.chatterdemo"
    label = "chatterdemo"
    default_auto_field = "django.db.models.BigAutoField"
