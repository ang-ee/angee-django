"""AppConfig for the hierarchy-mixin demo (test-only installed app).

Registered in ``tests.settings`` so pytest-django creates the demo tables that
exercise :class:`~angee.base.mixins.HierarchyMixin` — materialized-path subtree
queries, reparent cascade, cycle and company-boundary rejection, and the
prefix-serving pattern-ops index. The app carries no ``addon.toml``; it is a
plain Django app, not an Angee addon, so the composer and schema discovery ignore
it.
"""

from __future__ import annotations

from django.apps import AppConfig


class HierDemoConfig(AppConfig):
    """Installed app hosting the hierarchy-mixin demo models."""

    name = "tests.hierdemo"
    label = "hierdemo"
    default_auto_field = "django.db.models.BigAutoField"
