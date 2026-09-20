"""Native manifest binding and import boundaries for Django addon configs."""

from __future__ import annotations

import importlib
import importlib.util
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
    manifest: AddonManifest


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
        try:
            spec = importlib.util.find_spec(entry_point.module)
        except (ImportError, AttributeError, ValueError) as error:
            raise ImproperlyConfigured(f"Cannot resolve addon entry point {entry_point.value!r}") from error
        roots = tuple(Path(path) for path in spec.submodule_search_locations or ()) if spec else ()
        if not roots and spec is not None and spec.origin:
            roots = (Path(spec.origin).parent,)
        markers = tuple(root / "addon.toml" for root in roots if (root / "addon.toml").is_file())
        if len(markers) != 1:
            raise ImproperlyConfigured(
                f"{entry_point.value}: expected one import-package addon.toml, found {len(markers)}"
            )
        marker = markers[0]
        try:
            manifest = parse_manifest(marker)
        except (ManifestError, OSError) as error:
            raise ImproperlyConfigured(str(error)) from error
        if manifest.name != entry_point.name:
            raise ImproperlyConfigured(
                f"{marker}: addon.name {manifest.name!r} disagrees with entry point {entry_point.name!r}"
            )
        available[entry_point.name] = AvailableAddon(
            name=entry_point.name,
            source="installed",
            anchor=entry_point.value,
            manifest=manifest,
        )
    for addon_dir, manifest in discover(addon_dirs):
        available.setdefault(
            manifest.name,
            AvailableAddon(
                name=manifest.name, source="local", anchor=str(addon_dir), manifest=manifest
            ),
        )
    return dict(sorted(available.items()))


def resolve_manifest_roots(
    roots: Iterable[str],
    manifests: Iterable[AddonManifest],
    *,
    aliases: Mapping[str, str] | None = None,
) -> tuple[AddonManifest, ...]:
    """Resolve manifest roots into their deterministic dependency closure.

    Manifest-free Django/core roots remain outside this projection. Callers that
    already resolved AppConfig declarations supply their exact declaration aliases;
    this owner never guesses dotted AppConfig paths or imports disabled addons.
    """

    manifests_by_name: dict[str, AddonManifest] = {}
    for manifest in manifests:
        manifests_by_name.setdefault(manifest.name, manifest)
    ordered = order_app_dependencies(
        roots,
        {name: manifest.depends_on for name, manifest in manifests_by_name.items()},
        aliases=aliases,
    )
    return tuple(manifests_by_name[name] for name in ordered)


def order_app_dependencies(
    roots: Iterable[str],
    dependencies: Mapping[str, tuple[str, ...]],
    *,
    aliases: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Order a discovered app graph, rejecting duplicate edges and cycles.

    Visit roots in declaration order and each app's dependencies lexically,
    completing a dependency's closure before the next sibling or root. This
    precedence determines composed addon contributions. Only nodes supplied by
    the discovery owner participate: manifest projections omit plain Django apps,
    while AppGraph imports and validates every dependency before ordering.
    """

    app_aliases = aliases or {}
    root_names = tuple(roots)
    if len(set(root_names)) != len(root_names):
        duplicate = next(name for name in root_names if root_names.count(name) > 1)
        raise RuntimeError(f"Duplicate root app {duplicate!r}")
    ordered: list[str] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(declaration: str) -> None:
        name = app_aliases.get(declaration, declaration)
        if name not in dependencies or name in visited:
            return
        if name in visiting:
            cycle = " -> ".join((*visiting[visiting.index(name) :], name))
            raise RuntimeError(f"Cycle in app dependencies: {cycle}")
        declared_dependencies = dependencies[name]
        if len(set(declared_dependencies)) != len(declared_dependencies):
            raise RuntimeError(f"{name} declares duplicate dependency")
        visiting.append(name)
        for dependency in sorted(declared_dependencies):
            visit(dependency)
        visiting.pop()
        visited.add(name)
        ordered.append(name)

    for root in root_names:
        visit(root)
    return tuple(ordered)


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
