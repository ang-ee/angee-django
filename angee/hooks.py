"""Resolve declared Django-setting hooks without importing model implementations."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def _hook_paths(setting: str) -> tuple[str, ...]:
    """Read one single or ordered multi-hook declaration without importing it."""

    configured = getattr(settings, setting, "")
    if isinstance(configured, str):
        paths = (configured,) if configured else ()
    elif isinstance(configured, list | tuple) and all(isinstance(path, str) for path in configured):
        paths = tuple(configured)
    else:
        raise ImproperlyConfigured(f"settings.{setting} must be a dotted path or a list of dotted paths.")
    if not all(paths):
        raise ImproperlyConfigured(f"settings.{setting} contains an empty hook path.")
    return paths


def _resolve_hook_path(setting: str, path: str) -> Callable[..., Any]:
    """Import and validate one hook path from trusted settings."""

    try:
        hook = import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(f"settings.{setting} hook {path!r} cannot be imported: {error}") from error
    if not callable(hook):
        raise ImproperlyConfigured(f"settings.{setting} hook {path!r} is not callable.")
    return hook


def resolve_hooks(setting: str) -> tuple[Callable[..., Any], ...]:
    """Resolve one declared hook list in its configured order."""

    return tuple(_resolve_hook_path(setting, path) for path in _hook_paths(setting))


def resolve_hook(setting: str) -> Callable[..., Any] | None:
    """Resolve one optional hook from a setting."""

    paths = _hook_paths(setting)
    if len(paths) > 1:
        raise ImproperlyConfigured(f"settings.{setting} must name at most one hook.")
    return _resolve_hook_path(setting, paths[0]) if paths else None


def check_hook(
    name: str,
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Validate the same optional-single contract used by the actual caller."""

    del app_configs, kwargs
    try:
        resolve_hook(name)
    except ImproperlyConfigured as error:
        return [checks.Error(str(error), id="angee.E033")]
    return []


def check_hooks(
    names: Sequence[str],
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Validate the callable settings explicitly owned by one installed capability."""

    del app_configs, kwargs
    errors: list[checks.CheckMessage] = []
    for name in names:
        try:
            paths = _hook_paths(name)
        except ImproperlyConfigured as error:
            errors.append(checks.Error(str(error), id="angee.E033"))
            continue
        for path in paths:
            try:
                _resolve_hook_path(name, path)
            except ImproperlyConfigured as error:
                errors.append(checks.Error(str(error), id="angee.E033"))
    return errors
