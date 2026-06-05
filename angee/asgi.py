"""Stable ASGI entrypoint for composed Angee runtimes."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from django.apps import AppConfig, apps
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

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
    """Point Django at Angee's composed settings module."""

    project_dir = _project_dir()
    if project_dir is not None:
        os.environ.setdefault(_PROJECT_DIR_ENV, str(project_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")


def _websocket_urlpatterns() -> list[object]:
    """Return WebSocket URL patterns contributed by installed addons."""

    patterns: list[object] = []
    for app_config in apps.get_app_configs():
        patterns.extend(_addon_websocket_urlpatterns(app_config))
    return patterns


def _addon_websocket_urlpatterns(app_config: AppConfig) -> list[object]:
    """Return WebSocket URL patterns from one addon's conventional ``asgi.py``."""

    if not hasattr(app_config, "depends_on"):
        return []
    if not module_has_submodule(app_config.module, "asgi"):
        return []
    module_path = f"{app_config.name}.asgi"
    try:
        module = importlib.import_module(module_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{module_path} failed to import") from error
    contribution = getattr(module, "websocket_urlpatterns", None)
    if contribution is None:
        return []
    patterns = cast(Callable[[], object], contribution)() if callable(contribution) else contribution
    if not isinstance(patterns, Iterable):
        raise ImproperlyConfigured(f"{module_path}.websocket_urlpatterns must be iterable or callable")
    return list(patterns)


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
