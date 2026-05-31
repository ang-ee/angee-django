"""Django app configuration for Angee build commands."""

from __future__ import annotations

from django.apps import AppConfig


class ComposeConfig(AppConfig):
    """Plain Django app config that hosts compose management commands."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.compose"
