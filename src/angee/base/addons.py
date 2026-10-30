"""Read capability-owned TOML and conventional modules from native addon packages.

Distribution metadata and django-compose own addon identity and activation. This
module only exposes package data; it never resolves an application graph.
"""
from __future__ import annotations

import importlib
import json
import tomllib
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule
from django_compose.components import InstalledComponents, installed_components


def addon_facts(
    app_name: str, capability: str, *, index: InstalledComponents | None = None,
) -> dict[str, Any]:
    """Read ``[tool.<capability>]`` for a group member without importing it.

    Each capability supplies its own app name, including third-party names.
    Ordinary Django apps contribute no package facts. The compiler validates
    installed identities before enabled capabilities consume these facts. Collection
    callers pass one native index for their operation; standalone reads discover
    current metadata without retaining a process-global cache.
    """
    installed = installed_components("angee.addons") if index is None else index
    provider = installed.providers.get(app_name)
    if provider is None:
        return {}
    return capability_facts(package_document(installed.distributions[provider], app_name), capability)


def capability_facts(document: Mapping[str, Any], capability: str) -> dict[str, Any]:
    """Project ``[tool.<capability>]`` from installed or source package TOML."""

    facts: Any = document.get("tool", {})
    for name in capability.split("."):
        if not isinstance(facts, Mapping):
            raise ImproperlyConfigured(f"tool.{capability} must be a TOML table")
        facts = facts.get(name, {})
    if not isinstance(facts, Mapping):
        raise ImproperlyConfigured(f"tool.{capability} must be a TOML table")
    return dict(facts)


def package_document(dist: metadata.Distribution, app_name: str) -> dict[str, Any]:
    """Read packaged TOML through RECORD or a PEP 610 editable project URL."""

    suffix = "/".join((*app_name.split("."), "pyproject.toml"))
    paths = [dist.locate_file(path) for path in dist.files or () if path.as_posix() == suffix]
    if not paths and (direct_url := dist.read_text("direct_url.json")) is not None:
        source = json.loads(direct_url)
        url = urlsplit(source.get("url", ""))
        if source.get("dir_info", {}).get("editable") and url.scheme == "file" and url.netloc in ("", "localhost"):
            paths = [Path(url2pathname(url.path)) / "pyproject.toml"]
    if len(paths) != 1:
        raise ImproperlyConfigured(f"{app_name}: expected one packaged pyproject.toml, found {len(paths)}")
    try:
        return tomllib.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ImproperlyConfigured(f"{app_name}: cannot read package pyproject.toml") from error


def is_angee_addon(app_config: AppConfig, *, index: InstalledComponents | None = None) -> bool:
    """Read component identity, reusing the closure for already selected apps.

    A supplied index supports one discovery operation. Candidates outside the
    loaded closure still use installed metadata, so availability and activation
    remain separate facts. This reader also works before settings are configured.
    """

    if index is None:
        closure = getattr(settings, "COMPOSE_CLOSURE", None) if settings.configured else None
        if closure is not None and app_config.name in closure.dependencies:
            return app_config.name in closure.components.values()
        index = installed_components("angee.addons")
    return app_config.name in index.providers


def optional_addon_module(
    app_config: AppConfig, module_name: str, *, index: InstalledComponents | None = None,
) -> ModuleType | None:
    """Import an optional addon module, preserving errors in a present module."""

    if not is_angee_addon(app_config, index=index) or not module_has_submodule(app_config.module, module_name):
        return None
    module_path = f"{app_config.name}.{module_name}"
    try:
        return importlib.import_module(module_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{module_path} failed to import") from error
