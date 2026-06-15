"""Plain AppConfig helpers for Angee addon declarations."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


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
