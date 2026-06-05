"""Plain AppConfig helpers for Angee addon declarations."""

from __future__ import annotations

from django.apps import AppConfig


def is_angee_addon(app_config: AppConfig) -> bool:
    """Return whether ``app_config`` opts into Angee addon discovery."""

    return getattr(app_config, "angee_addon", False) is True
