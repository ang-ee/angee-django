"""Native manifest binding and import boundaries for Django addon configs."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string, module_has_submodule
from hatch_angee import AddonManifest, ManifestError, discover, parse_manifest

ADDON_ENTRY_POINT_GROUP = "angee.addons"
_MANIFEST_CACHE = "_angee_manifest"


def addon_manifest(app_config: AppConfig, *, refresh: bool = False) -> AddonManifest | None:
    """Bind the authoritative manifest to this native config for one composition.

    The stored value is the unchanged upstream parser result, never configurable
    AppConfig declarations or inferred defaults. AppGraph refreshes the binding at
    the start of each composition, even when a caller reuses config instances.
    Other readers share that same object throughout the run. Plain Django apps
    without an addon marker return None.
    """

    if refresh:
        app_config.__dict__.pop(_MANIFEST_CACHE, None)
    if _MANIFEST_CACHE in app_config.__dict__:
        return app_config.__dict__[_MANIFEST_CACHE]
    root = getattr(app_config, "path", None)
    marker = Path(root) / "addon.toml" if root is not None else None
    manifest = None
    if marker is not None and marker.is_file():
        try:
            manifest = parse_manifest(marker)
        except (ManifestError, OSError) as error:
            raise ImproperlyConfigured(str(error)) from error
        if manifest.name != app_config.name:
            raise ImproperlyConfigured(
                f"{marker}: addon.name {manifest.name!r} disagrees with AppConfig.name {app_config.name!r}"
            )
        if len(set(manifest.depends_on)) != len(manifest.depends_on):
            raise ImproperlyConfigured(f"{manifest.name} declares duplicate dependency in addon.depends_on")
    app_config.__dict__[_MANIFEST_CACHE] = manifest
    return manifest


@dataclass(frozen=True, slots=True)
class AvailableAddon:
    """An addon present in the environment, whether or not it is enabled.

    ``source`` is ``"installed"`` for an addon advertised by an installed bundle's
    ``angee.addons`` entry point, or ``"local"`` for one discovered as an
    ``addon.toml`` under a configured addon dir. ``anchor`` is the entry point's
    import target (installed) or the addon directory (local).
    """

    name: str
    source: str
    anchor: str


def available_addons(addon_dirs: Iterable[Path | str] = ()) -> dict[str, AvailableAddon]:
    """Return every *available* addon, keyed by name.

    The available set is the union of (1) the ``angee.addons`` entry points across
    all installed distributions — the SSOT being ``uv.lock``'s bundles, the same
    way ``pip``-installed packages are "available" before being added to
    ``INSTALLED_APPS`` — and (2) any ``addon.toml`` under the configured addon dirs
    (local/uninstalled consumer addons). The enabled set (``INSTALLED_APPS``) is
    expected to be a subset of this. Pure ``importlib.metadata`` + filesystem; no
    Django app loading required, so a catalog/marketplace can read it cheaply.
    """

    available: dict[str, AvailableAddon] = {}
    for entry_point in metadata.entry_points(group=ADDON_ENTRY_POINT_GROUP):
        available[entry_point.name] = AvailableAddon(
            name=entry_point.name, source="installed", anchor=entry_point.value
        )
    for addon_dir, manifest in discover(addon_dirs):
        available.setdefault(
            manifest.name,
            AvailableAddon(name=manifest.name, source="local", anchor=str(addon_dir)),
        )
    return dict(sorted(available.items()))


def is_angee_addon(app_config: AppConfig) -> bool:
    """Return whether the native config has a co-located addon manifest."""

    return addon_manifest(app_config) is not None


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
    """Import the object a ``<attr>`` dotted reference on an addon names.

    A bare ``"module.name"`` is taken relative to the addon's import package
    (``app_config.name``); an already-qualified path is used as-is. Raises
    ``ImproperlyConfigured`` naming ``<addon>.<attr>`` on failure. The one owner of
    the manifest dotted-reference contract shared by the ``schemas`` (GraphQL) and
    ``mcp_tools`` (MCP) discovery seams — including the fail-fast that the reference
    is a dotted string in the first place.
    """

    if not isinstance(dotted, str):
        raise ImproperlyConfigured(f"{app_config.name}.{attr} must be a dotted reference")
    path = dotted if dotted.startswith(f"{app_config.name}.") else f"{app_config.name}.{dotted}"
    try:
        return import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{app_config.name}.{attr} references {path!r}") from error


def addon_contribution(
    app_config: AppConfig,
    module_name: str,
    attr: str,
    *,
    allow_callable: bool = False,
) -> list[Any]:
    """Return an installed addon's conventional iterable contribution.

    Addon subsystems expose small conventional modules such as ``urls.py`` or
    ``asgi.py``. This helper owns the repeated Angee-addon gate, submodule check,
    import error shape, optional callable execution, and iterable validation.
    """

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
