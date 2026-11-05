"""AppConfig for the installed local-scope and task test composition.

Registered in ``tests.settings`` so pytest-django creates the test tables on
demand and ``rebac sync`` discovers the adjacent ``permissions.zed`` (its
``.path`` is this package). The app carries no addon entry point — it is a plain
Django app, not an Angee addon, so the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class ScopeDemoConfig(AppConfig):
    """Installed app hosting the local scope, document and task models."""

    name = "tests.scopedemo"
    label = "scopedemo"
    default_auto_field = "django.db.models.BigAutoField"
