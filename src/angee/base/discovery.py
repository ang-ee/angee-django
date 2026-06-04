"""Addon discovery for runtime and build-time Angee callers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from django.apps import apps as django_apps
from django.apps.registry import Apps
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseAddonConfig


class _AppRegistry(Protocol):
    """Registry shape needed to enumerate Django app configs."""

    def get_app_configs(self) -> Iterable[object]:
        """Return the registry's installed app configs."""


def discover_addons(
    apps: Apps | _AppRegistry | None = None,
) -> tuple[BaseAddonConfig, ...]:
    """Return installed Angee addon configs in dependency order."""

    registry = apps or django_apps
    addons: list[BaseAddonConfig] = []
    seen_names: set[str] = set()
    for app_config in registry.get_app_configs():
        if not isinstance(app_config, BaseAddonConfig):
            continue
        if app_config.name in seen_names:
            raise ImproperlyConfigured(f"Duplicate Angee addon {app_config.name!r}")
        seen_names.add(app_config.name)
        addons.append(app_config)
    return _sort_addons(tuple(addons))


def _sort_addons(
    addons: tuple[BaseAddonConfig, ...],
) -> tuple[BaseAddonConfig, ...]:
    """Return addons sorted by dependency aliases with cycle detection."""

    by_name = {addon.name: addon for addon in addons}
    aliases = addon_aliases(addons)
    ordered: list[BaseAddonConfig] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        """Visit one addon and then append it after its dependencies."""

        if name in visited:
            return
        if name in visiting:
            raise ImproperlyConfigured(f"Cycle in addon dependencies at {name}")
        visiting.add(name)
        addon = by_name[name]
        for dependency in sorted(addon.dependencies):
            dependency_name = aliases.get(dependency)
            if dependency_name is None:
                raise ImproperlyConfigured(f"{addon.name} depends on unknown addon {dependency!r}")
            visit(dependency_name)
        visiting.remove(name)
        visited.add(name)
        ordered.append(addon)

    for name in sorted(by_name):
        visit(name)
    return tuple(ordered)


def addon_aliases(addons: Iterable[BaseAddonConfig]) -> dict[str, str]:
    """Return addon names and labels mapped to canonical addon names."""

    aliases: dict[str, str] = {}
    for addon in addons:
        for alias in (addon.name, addon.label):
            existing = aliases.setdefault(alias, addon.name)
            if existing != addon.name:
                raise ImproperlyConfigured(f"Duplicate addon alias {alias!r}")
    return aliases
