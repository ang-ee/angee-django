"""AppConfig for the record-reference probes (test-only installed app).

Registered in ``tests.settings`` so pytest-django creates the probe tables that
exercise :class:`~angee.base.models.refs.RecordRefMixin`: typed and untyped targets,
edges with default, subject and custom column names, and nullable pointers. It
also hosts the rows a merge moves that no addon model has: many-to-many links
to a record's own model, and a key under an expression constraint.
Code that inventories installed models (a delete's collector, a merge's
references) finds every probe with its table. The app carries no
addon entry point; it is a plain Django app, not an Angee addon, so the composer
and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class RecordRefDemoConfig(AppConfig):
    """Installed app hosting the record-reference probe models."""

    name = "tests.recordrefdemo"
    label = "recordrefdemo"
    default_auto_field = "django.db.models.BigAutoField"
