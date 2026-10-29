"""Installed addon identity, requirement activation and package-data readers."""

from __future__ import annotations

import importlib
import json
import tomllib
from collections.abc import Iterable, Mapping
from importlib import metadata, resources
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string, module_has_submodule
from hatch_angee import order_app_dependencies
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ADDON_ENTRY_POINT_GROUP = "angee.addons"


def addon_entry_points() -> dict[str, metadata.EntryPoint]:
    """Index installed addon identities without importing their Python packages.

    Each distribution contributes exactly one app. Both duplicate app identities
    and distributions advertising multiple apps are configuration errors.
    """

    result: dict[str, metadata.EntryPoint] = {}
    distributions: set[str] = set()
    for entry in sorted(metadata.entry_points(group=ADDON_ENTRY_POINT_GROUP), key=lambda item: (item.name, item.value)):
        if entry.name in result:
            raise ImproperlyConfigured(f"Duplicate installed addon entry point {entry.name!r}")
        if entry.dist is None:
            raise ImproperlyConfigured(f"Addon entry point {entry.name!r} has no distribution")
        distribution = canonicalize_name(entry.dist.metadata["Name"])
        if distribution in distributions:
            raise ImproperlyConfigured(f"Distribution {distribution!r} declares multiple addon entry points")
        distributions.add(distribution)
        result[entry.name] = entry
    return result


def _dependency_names(requirements: Iterable[str], dependencies: Mapping[str, str]) -> tuple[str, ...]:
    """Project native requirements active without extras onto addon identities."""

    names: set[str] = set()
    lookup = {canonicalize_name(name): app for name, app in dependencies.items()}
    for value in requirements:
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            if (name := lookup.get(canonicalize_name(requirement.name))) is not None:
                names.add(name)
    return tuple(sorted(names))


def _distribution_names() -> dict[str, str]:
    return {
        canonicalize_name(entry.dist.metadata["Name"]): name
        for name, entry in addon_entry_points().items() if entry.dist is not None
    }


def _identity(document: Mapping[str, Any]) -> tuple[str, str] | None:
    entries = document.get("project", {}).get("entry-points", {}).get(ADDON_ENTRY_POINT_GROUP, {})
    if not entries:
        return None
    if not isinstance(entries, Mapping) or len(entries) != 1:
        raise ImproperlyConfigured("An addon distribution must declare exactly one angee.addons entry point")
    name, value = next(iter(entries.items()))
    if not isinstance(name, str) or not isinstance(value, str):
        raise ImproperlyConfigured("Addon entry-point names and values must be strings")
    return name, value


def _tool_facts(document: Mapping[str, Any]) -> dict[str, Any]:
    facts = document.get("tool", {}).get("angee", {})
    if not isinstance(facts, Mapping):
        raise ImproperlyConfigured("tool.angee must be a TOML table")
    return dict(facts)


def facts_from_toml(
    document: Mapping[str, Any], *, dependencies: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project catalogue facts from package TOML, skipping ordinary projects.

    ``dependencies`` maps distribution names to declared addon app identities.
    A VCS catalogue supplies its complete document index; installed callers use
    the native entry-point index. Capability tables remain owned by their addons.
    """

    identity = _identity(document)
    if identity is None:
        return {}
    name, _value = identity
    project = document["project"]
    distribution = project.get("name")
    if not isinstance(distribution, str) or canonicalize_name(distribution) != canonicalize_name(name):
        raise ImproperlyConfigured(f"Addon {name!r} must use its app name as its distribution name")
    return {
        "name": name,
        "distribution": distribution,
        "description": project.get("description", ""),
        "keywords": tuple(project.get("keywords", ())),
        "category": _tool_facts(document).get("category", ""),
        "depends_on": _dependency_names(
            project.get("dependencies", ()), _distribution_names() if dependencies is None else dependencies,
        ),
    }


def _package_document(dist: metadata.Distribution, app_name: str) -> dict[str, Any]:
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


def facts_from_distribution(dist: metadata.Distribution) -> dict[str, Any]:
    """Read a disabled addon's catalogue facts without importing its package.

    Native distribution metadata owns published requirements and presentation;
    package data contributes the catalogue's Angee-only category.
    """

    entries = [entry for entry in dist.entry_points if entry.group == ADDON_ENTRY_POINT_GROUP]
    if not entries:
        return {}
    if len(entries) != 1:
        raise ImproperlyConfigured("An addon distribution must declare exactly one angee.addons entry point")
    entry = entries[0]
    document = _package_document(dist, entry.name)
    if _identity(document) != (entry.name, entry.value):
        raise ImproperlyConfigured(f"{entry.name}: package TOML disagrees with installed entry-point identity")
    project = {
        **document["project"],
        "name": dist.metadata["Name"],
        "description": dist.metadata.get("Summary", ""),
        "keywords": tuple(part.strip() for part in dist.metadata.get("Keywords", "").split(",") if part.strip()),
        "dependencies": dist.requires or (),
    }
    return facts_from_toml({**document, "project": project})


def addon_packages() -> dict[str, dict[str, Any]]:
    """Return installed catalogue projections in deterministic app order."""

    return {
        name: facts_from_distribution(entry.dist)
        for name, entry in addon_entry_points().items() if entry.dist is not None
    }


def addon_facts(app_name: str) -> dict[str, Any]:
    """Read an enabled addon's tool.angee tables without interpreting capabilities."""

    if app_name not in addon_entry_points():
        return {}
    try:
        document = tomllib.loads(resources.files(app_name).joinpath("pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ImproperlyConfigured(f"{app_name}: cannot read package pyproject.toml") from error
    if (identity := _identity(document)) is None or identity[0] != app_name:
        raise ImproperlyConfigured(f"{app_name}: package TOML disagrees with its app identity")
    return _tool_facts(document)


def addon_dependencies(app_config: AppConfig, *, refresh: bool = False) -> tuple[str, ...]:
    """Return active addon requirements and explicit foreign Django app edges.

    No mutable binding is kept on AppConfig, so ``refresh`` needs no invalidation.
    Entry-point values retain a dependency's selected native config class.
    """

    entries = addon_entry_points()
    entry = entries.get(app_config.name)
    names = () if entry is None or entry.dist is None else _dependency_names(
        entry.dist.requires or (),
        {item.dist.metadata["Name"]: name for name, item in entries.items() if item.dist is not None},
    )
    required = getattr(app_config, "requires_apps", ())
    if not isinstance(required, (tuple, list)) or not all(isinstance(name, str) for name in required):
        raise ImproperlyConfigured(f"{app_config.name}.requires_apps must be a list or tuple of app paths")
    return (*tuple(entries[name].value for name in names), *required)


def resolve_package_roots(
    roots: Iterable[str], packages: Iterable[Mapping[str, Any]], *, aliases: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Project a catalogue closure through the compiler's existing order owner."""

    by_name = {package["name"]: package for package in packages}
    resolved_aliases = {entry.value: name for name, entry in addon_entry_points().items()}
    resolved_aliases.update(aliases or {})
    ordered = order_app_dependencies(
        roots, {name: package["depends_on"] for name, package in by_name.items()}, aliases=resolved_aliases,
    )
    return tuple(by_name[name] for name in ordered if name in by_name)


def is_angee_addon(app_config: AppConfig) -> bool:
    """Return whether installed distribution metadata declares this addon."""

    return app_config.name in addon_entry_points()


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
