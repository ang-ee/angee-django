"""Resolve declared Django-setting hooks without importing model implementations."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def declared_hooks() -> tuple[str, ...]:
    """Return hook setting names declared by installed addons."""

    declared = getattr(settings, "ANGEE_HOOKS", ())
    if not isinstance(declared, list | tuple) or not all(isinstance(name, str) for name in declared):
        raise ImproperlyConfigured("settings.ANGEE_HOOKS must be a list of hook setting names.")
    return tuple(declared)


def _hook_paths(setting: str, *, sorted_unique: bool = False) -> tuple[str, ...]:
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
    return tuple(sorted(set(paths))) if sorted_unique else paths


def _resolve_hook_path(setting: str, path: str) -> Callable[..., Any]:
    """Import and validate one hook path from trusted settings."""

    try:
        hook = import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(f"settings.{setting} hook {path!r} cannot be imported: {error}") from error
    if not callable(hook):
        raise ImproperlyConfigured(f"settings.{setting} hook {path!r} is not callable.")
    return hook


def resolve_hooks(setting: str, *, sorted_unique: bool = False) -> tuple[Callable[..., Any], ...]:
    """Resolve an ordered hook list; owners may request sorted unique paths."""

    return tuple(_resolve_hook_path(setting, path) for path in _hook_paths(setting, sorted_unique=sorted_unique))


def resolve_hook(setting: str) -> Callable[..., Any] | None:
    """Resolve one optional hook from a setting."""

    paths = _hook_paths(setting)
    if len(paths) > 1:
        raise ImproperlyConfigured(f"settings.{setting} must name at most one hook.")
    return _resolve_hook_path(setting, paths[0]) if paths else None


def check_hooks(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Validate every callable hook declared by installed addons."""

    del app_configs, kwargs
    try:
        names = declared_hooks()
    except ImproperlyConfigured as error:
        return [checks.Error(str(error), id="angee.E033")]
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
