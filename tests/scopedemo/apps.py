"""AppConfig for the company-scope create-gate demo (test-only installed app).

Registered in ``tests.settings`` so pytest-django creates the demo table on
demand and ``rebac sync`` discovers the adjacent ``permissions.zed`` (its
``.path`` is this package). The app carries no ``addon.toml`` — it is a plain
Django app, not an Angee addon, so the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class ScopeDemoConfig(AppConfig):
    """Installed app hosting the company-scoped create-gated document model."""

    name = "tests.scopedemo"
    label = "scopedemo"
    default_auto_field = "django.db.models.BigAutoField"
