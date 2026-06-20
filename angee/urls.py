"""Stable URLConf for composed Angee runtimes."""

from __future__ import annotations

from django.apps import AppConfig, apps

from angee.addons import addon_contribution


def _addon_urlpatterns(app_config: AppConfig) -> list[object]:
    """Return URL patterns from one addon's conventional ``urls.py`` module."""

    return addon_contribution(app_config, "urls", "urlpatterns")


urlpatterns = [pattern for app_config in apps.get_app_configs() for pattern in _addon_urlpatterns(app_config)]
"""URL patterns contributed by installed addons in dependency order."""
