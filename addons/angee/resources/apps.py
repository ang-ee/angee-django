"""Django app configuration for Angee resource commands."""

from __future__ import annotations

from django.apps import AppConfig


class ResourcesConfig(AppConfig):
    """Resource addon marker and command/model host."""

    default = True
    angee_addon = True
    name = "angee.resources"
    depends_on = ("angee.base",)
    schemas = "schema.schemas"
