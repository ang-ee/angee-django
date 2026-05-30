"""Discover installed addon configs in deterministic composition order."""

from __future__ import annotations

from django.apps import apps as django_apps
from django.apps.registry import Apps
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseAddonConfig


def discover_addons(apps: Apps | None = None) -> tuple[BaseAddonConfig, ...]:
    """Return installed Angee addon configs in dependency order."""

    registry = apps or django_apps
    addons: list[BaseAddonConfig] = []
    seen_names: set[str] = set()
    for app_config in registry.get_app_configs():
        if not isinstance(app_config, BaseAddonConfig):
            continue
        if app_config.name in seen_names:
            raise ImproperlyConfigured(
                f"Duplicate Angee addon {app_config.name!r}"
            )
        seen_names.add(app_config.name)
        addons.append(app_config)
    return _toposort(tuple(addons))


def _toposort(
    addons: tuple[BaseAddonConfig, ...],
) -> tuple[BaseAddonConfig, ...]:
    """Sort addons by dependency labels and names with cycle detection."""

    by_name = {addon.name: addon for addon in addons}
    aliases: dict[str, str] = {}
    for addon in addons:
        for alias in (addon.name, addon.label):
            previous = aliases.setdefault(alias, addon.name)
            if previous != addon.name:
                raise ImproperlyConfigured(f"Duplicate addon alias {alias!r}")

    result: list[BaseAddonConfig] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ImproperlyConfigured(
                f"Cycle in addon dependencies at {name}"
            )
        visiting.add(name)
        addon = by_name[name]
        for dependency in sorted(addon.dependencies):
            dependency_name = aliases.get(dependency)
            if dependency_name is None:
                raise ImproperlyConfigured(
                    f"{addon.name} depends on unknown addon {dependency!r}"
                )
            _visit(dependency_name)
        visiting.remove(name)
        visited.add(name)
        result.append(addon)

    for name in sorted(by_name):
        _visit(name)
    return tuple(result)
