"""Django config for the parties addon."""

from __future__ import annotations

from django.apps import AppConfig


class PartiesConfig(AppConfig):
    """Source app manifest for the parties addon."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.parties"
    label = "parties"
    depends_on = (
        "angee.iam",
        "angee.integrate",
        "angee.storage",
    )
    schemas = "schema.schemas"
    permissions = "permissions.zed"
