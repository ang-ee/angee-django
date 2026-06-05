"""Django config for Angee's integration runtime addon."""

from __future__ import annotations

from django.apps import AppConfig


class IntegrateConfig(AppConfig):
    """Source app manifest for Angee integration runtime primitives."""

    default = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.integrate"
    label = "integrate"
    depends_on = ("angee.iam",)
    permissions = "permissions.zed"
