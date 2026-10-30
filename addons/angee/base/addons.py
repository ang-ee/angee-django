"""Read capability-owned TOML and conventional modules from native addon packages.

Distribution metadata and django-compose own addon identity and activation. This
module only exposes package data; it never resolves an application graph.
"""
from __future__ import annotations

import importlib
import json
import tomllib
from collections.abc import Iterable, Mapping
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string, module_has_submodule
from django_compose.addons import installed_addons


def addon_facts(app_name: str, capability: str) -> dict[str, Any]:
    """Read ``[tool.<capability>]`` for a group member without importing it.

    Each capability supplies its own app name, including third-party names.
    Ordinary Django apps contribute no package facts. The compiler validates
    installed identities before enabled capabilities consume these facts.
    """
    installed = installed_addons("angee.addons")
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


def is_angee_addon(app_config: AppConfig) -> bool:
    """Return whether installed distribution metadata declares this addon."""

    return app_config.name in installed_addons("angee.addons").providers


def optional_addon_module(app_config: AppConfig, module_name: str) -> ModuleType | None:
    """Import an optional addon module, preserving errors in a present module."""

    if not is_angee_addon(app_config) or not module_has_submodule(app_config.module, module_name):
        return None
    module_path = f"{app_config.name}.{module_name}"
    try:
        return importlib.import_module(module_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{module_path} failed to import") from error


def resolve_addon_reference(app_config: AppConfig, dotted: str, *, attr: str) -> Any:
    """Resolve a capability-owned dotted reference relative to its addon package."""

    if not isinstance(dotted, str):
        raise ImproperlyConfigured(f"{app_config.name}.{attr} must be a dotted reference")
    path = dotted if dotted.startswith(f"{app_config.name}.") else f"{app_config.name}.{dotted}"
    try:
        return import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{app_config.name}.{attr} references {path!r}") from error


def addon_contribution(
    app_config: AppConfig, module_name: str, attr: str, *, allow_callable: bool = False,
) -> list[Any]:
    """Return an installed addon's conventional iterable contribution."""

    module = optional_addon_module(app_config, module_name)
    if module is None:
        return []
    module_path = module.__name__
    if not hasattr(module, attr):
        return []
    contribution = getattr(module, attr)
    value = contribution() if allow_callable and callable(contribution) else contribution
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | Mapping | set | frozenset):
        suffix = "iterable or callable" if allow_callable else "iterable"
        raise ImproperlyConfigured(f"{module_path}.{attr} must be {suffix}")
    return list(value)
