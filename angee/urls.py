"""Stable URLConf for composed Angee runtimes."""

from __future__ import annotations

from collections.abc import Iterable

from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def _addon_urlpatterns(app_config: AppConfig) -> list[object]:
    """Return URL patterns declared by one addon."""

    declaration = getattr(app_config, "url_patterns", None)
    if declaration is None:
        return []
    patterns = _declared_object(app_config, "url_patterns", declaration)
    if not isinstance(patterns, Iterable):
        raise ImproperlyConfigured(f"{app_config.name}.url_patterns must reference an iterable")
    return list(patterns)


def _declared_object(app_config: AppConfig, attribute: str, declaration: object) -> object:
    """Import one object declared on an app config."""

    if not isinstance(declaration, str):
        raise ImproperlyConfigured(f"{app_config.name}.{attribute} must be a dotted import string")
    dotted_path = declaration if declaration.startswith(f"{app_config.name}.") else f"{app_config.name}.{declaration}"
    try:
        return import_string(dotted_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{app_config.name}.{attribute} references {dotted_path!r}") from error


urlpatterns = [pattern for app_config in apps.get_app_configs() for pattern in _addon_urlpatterns(app_config)]
"""URL patterns contributed by installed addons in dependency order."""
