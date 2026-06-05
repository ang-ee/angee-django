"""Stable ASGI entrypoint for composed Angee runtimes."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from django.apps import AppConfig, apps
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

_PROJECT_DIR_ENV = "ANGEE_PROJECT_DIR"


def _project_dir() -> Path | None:
    """Return the project root for direct ASGI imports, when discoverable."""

    configured = os.environ.get(_PROJECT_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    for parent in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        if (parent / "settings.yaml").exists() or (parent / "settings.py").exists():
            return parent
    return None


def _bootstrap() -> None:
    """Make Angee project settings importable before Django builds the app."""

    project_dir = _project_dir()
    if project_dir is not None:
        os.environ.setdefault(_PROJECT_DIR_ENV, str(project_dir))
        _prepend_import_paths((project_dir / "addons", project_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")


def _prepend_import_paths(paths: Iterable[Path]) -> None:
    """Place existing paths at the front of ``sys.path`` preserving order."""

    normalized = tuple(str(path.resolve()) for path in paths if path.exists())
    for path in reversed(normalized):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


def _websocket_urlpatterns() -> list[object]:
    """Return WebSocket URL patterns contributed by installed addons."""

    patterns: list[object] = []
    for app_config in apps.get_app_configs():
        patterns.extend(_addon_websocket_urlpatterns(app_config))
    return patterns


def _addon_websocket_urlpatterns(app_config: AppConfig) -> list[object]:
    """Return WebSocket URL patterns declared by one addon."""

    declaration = getattr(app_config, "asgi_websocket_urlpatterns", None)
    if declaration is None:
        return []
    contribution = _declared_object(app_config, "asgi_websocket_urlpatterns", declaration)
    patterns = cast(Callable[[], object], contribution)() if callable(contribution) else contribution
    if not isinstance(patterns, Iterable):
        raise ImproperlyConfigured(
            f"{app_config.name}.asgi_websocket_urlpatterns must reference an iterable or callable"
        )
    return list(patterns)


def _declared_object(app_config: AppConfig, attribute: str, declaration: object) -> object:
    """Import one object declared on an app config."""

    if not isinstance(declaration, str):
        raise ImproperlyConfigured(f"{app_config.name}.{attribute} must be a dotted import string")
    dotted_path = declaration if declaration.startswith(f"{app_config.name}.") else f"{app_config.name}.{declaration}"
    try:
        return import_string(dotted_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{app_config.name}.{attribute} references {dotted_path!r}") from error


def _application() -> Any:
    """Build the ASGI application after settings and apps are ready."""

    django_asgi_app = get_asgi_application()
    websocket_patterns = _websocket_urlpatterns()
    if not websocket_patterns:
        return django_asgi_app

    from channels.auth import AuthMiddlewareStack
    from channels.routing import ProtocolTypeRouter, URLRouter

    return ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_patterns)),
        }
    )


_bootstrap()
application = _application()
"""ASGI application for the composed Angee runtime."""
