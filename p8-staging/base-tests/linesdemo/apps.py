"""AppConfig for the F6 editable-lines demo (test-only installed app).

Registered in ``tests.settings`` so pytest-django creates the demo tables and
``rebac sync`` discovers the adjacent ``permissions.zed`` (its ``.path`` is this
package). The app carries no ``addon.toml`` — it is a plain Django app, not an
Angee addon, so the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class LinesDemoConfig(AppConfig):
    """Installed app hosting the F6 demo document + line models."""

    name = "tests.linesdemo"
    label = "linesdemo"
    default_auto_field = "django.db.models.BigAutoField"
