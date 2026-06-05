"""Django app configuration for Angee's model foundation."""

from __future__ import annotations

from django.apps import AppConfig


class BaseConfig(AppConfig):
    """Django app configuration for Angee's model foundation."""

    default = True
    angee_addon = True
    name = "angee.base"
    label = "base"
    depends_on = (
        "angee.compose",
        "django.contrib.contenttypes",
        "rebac",
        "reversion",
        "simple_history",
    )
