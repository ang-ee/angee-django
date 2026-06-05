"""Stable URLConf for composed Angee runtimes."""

from __future__ import annotations

import importlib
from collections.abc import Iterable

from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

from angee.addons import is_angee_addon


def _addon_urlpatterns(app_config: AppConfig) -> list[object]:
    """Return URL patterns from one addon's conventional ``urls.py`` module."""

    if not is_angee_addon(app_config):
        return []
    if not module_has_submodule(app_config.module, "urls"):
        return []
    module_path = f"{app_config.name}.urls"
    try:
        module = importlib.import_module(module_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{module_path} failed to import") from error
    patterns = getattr(module, "urlpatterns", None)
    if patterns is None:
        return []
    if not isinstance(patterns, Iterable):
        raise ImproperlyConfigured(f"{module_path}.urlpatterns must be iterable")
    return list(patterns)


urlpatterns = [pattern for app_config in apps.get_app_configs() for pattern in _addon_urlpatterns(app_config)]
"""URL patterns contributed by installed addons in dependency order."""
