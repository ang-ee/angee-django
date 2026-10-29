"""Stable URLConf for composed Angee runtimes."""

from __future__ import annotations

from django.apps import apps

from angee.addons import addon_contribution

urlpatterns = [
    pattern
    for app_config in apps.get_app_configs()
    for pattern in addon_contribution(app_config, "urls", "urlpatterns")
]
"""URL patterns contributed by installed addons in dependency order."""
