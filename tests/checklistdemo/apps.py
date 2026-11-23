"""AppConfig for the checklist probes (test-only installed app).

Registered in ``tests.settings`` so pytest-django creates a checklist type, an
evidence type and their permission declarations: the app declares its target
and evidence relations on the checklists definitions from its
``permissions.extends.zed``, as a consumer addon does. It carries no addon entry
point; the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class ChecklistDemoConfig(AppConfig):
    """Installed app hosting the checklist probe models."""

    name = "tests.checklistdemo"
    label = "checklistdemo"
    # Its rules lend no row through a link (angee.base.permissions.undeclared_policy_apps).
    rebac_evaluated_policy = True
    default_auto_field = "django.db.models.BigAutoField"
