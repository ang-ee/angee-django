"""AppConfig for the additive zed-extension demo contributor (test-only app).

The example contributes ``scopedemo/doc`` review access and account actions
through ``permissions.extends.zed``. Its own ``extcontrib/role`` definition
derives account authority from a bound team's roster. The composer merges the
fragments; the library reads the resulting schemas and the role's base schema.
"""

from __future__ import annotations

from django.apps import AppConfig


class ExtContribConfig(AppConfig):
    """Installed example of additive permissions and a roster-backed role."""

    name = "tests.extcontrib"
    label = "extcontrib"
    default_auto_field = "django.db.models.BigAutoField"
