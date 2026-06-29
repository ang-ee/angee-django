"""Plain AppConfig helpers for Angee addon declarations."""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string, module_has_submodule

ADDON_ENTRY_POINT_GROUP = "angee.addons"


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
    for addon_dir in addon_dirs:
        for marker in sorted(Path(addon_dir).glob("**/addon.toml")):
            if "node_modules" in marker.parts:
                continue
            name = tomllib.loads(marker.read_text()).get("addon", {}).get("name")
            if name and name not in available:
                available[name] = AvailableAddon(name=name, source="local", anchor=str(marker.parent))
    return dict(sorted(available.items()))


def is_angee_addon(app_config: AppConfig) -> bool:
    """Return whether ``app_config`` opts into Angee addon discovery."""

    return getattr(app_config, "angee_addon", False) is True


def resolve_addon_reference(app_config: AppConfig, dotted: str, *, attr: str) -> Any:
    """Import the object a ``<attr>`` dotted reference on an addon names.

    A bare ``"module.name"`` is taken relative to the addon's import package
    (``app_config.name``); an already-qualified path is used as-is. Raises
    ``ImproperlyConfigured`` naming ``<addon>.<attr>`` on failure. The one owner of
    the manifest dotted-reference contract shared by the ``schemas`` (GraphQL) and
    ``mcp_tools`` (MCP) discovery seams.
    """

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

    if not is_angee_addon(app_config):
        return []
    if not module_has_submodule(app_config.module, module_name):
        return []
    module_path = f"{app_config.name}.{module_name}"
    try:
        module = importlib.import_module(module_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{module_path} failed to import") from error
    contribution = getattr(module, attr, None)
    if contribution is None:
        return []
    value = contribution() if allow_callable and callable(contribution) else contribution
    if not isinstance(value, Iterable):
        suffix = "iterable or callable" if allow_callable else "iterable"
        raise ImproperlyConfigured(f"{module_path}.{attr} must be {suffix}")
    return list(value)
