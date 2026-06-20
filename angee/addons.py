"""Plain AppConfig helpers for Angee addon declarations."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string, module_has_submodule


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
    value = contribution() if allow_callable and isinstance(contribution, Callable) else contribution
    if not isinstance(value, Iterable):
        suffix = "iterable or callable" if allow_callable else "iterable"
        raise ImproperlyConfigured(f"{module_path}.{attr} must be {suffix}")
    return list(value)
