"""Settings helper for small composed Django hosts."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseAddonConfig, BaseConfig


def compose_defaults(
    *,
    addons: Sequence[str],
    runtime_dir: Path,
    root_urlconf: str,
    asgi_application: str,
    data_dir: Path | None = None,
    runtime_module: str = "runtime",
    shells: Sequence[str] = ("public",),
    debug: bool = False,
    use_tz: bool = True,
    graphql_ide: str | None = None,
    migration_modules: Mapping[str, str] | None = None,
    static_url: str = "/static/",
    extra_installed_apps: Sequence[str] = (),
    extra_middleware: Sequence[str] = (),
    channel_layers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return Django setting values derived from Angee composition.

    ``ANGEE_RUNTIME_DIR`` and ``ANGEE_DATA_DIR`` environment variables override
    caller-provided paths. ``ANGEE_DATA_DIR`` is required unless ``data_dir`` is
    passed deliberately, so local commands do not create databases in surprising
    places.
    """

    runtime_dir = Path(os.environ.get("ANGEE_RUNTIME_DIR", runtime_dir))
    data_dir = _resolve_data_dir(data_dir)
    if str(runtime_dir.parent) not in sys.path:
        sys.path.insert(0, str(runtime_dir.parent))

    addon_entries = _addon_entries(addons)
    return {
        "INSTALLED_APPS": _dedupe(
            [
                "daphne",
                "channels",
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "rebac",
                f"{BaseConfig.__module__}.{BaseConfig.__name__}",
                *addon_entries,
                *extra_installed_apps,
            ]
        ),
        "MIDDLEWARE": [
            "django.middleware.common.CommonMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "rebac.middleware.ActorMiddleware",
            *extra_middleware,
        ],
        "AUTHENTICATION_BACKENDS": [
            "rebac.backends.auth.RebacBackend",
            "django.contrib.auth.backends.ModelBackend",
        ],
        "CHANNEL_LAYERS": channel_layers
        or {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        "ROOT_URLCONF": root_urlconf,
        "ASGI_APPLICATION": asgi_application,
        "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
        "USE_TZ": use_tz,
        "ANGEE_RUNTIME_DIR": runtime_dir,
        "ANGEE_DATA_DIR": data_dir,
        "ANGEE_RUNTIME_MODULE": runtime_module,
        "ANGEE_SHELLS": tuple(dict.fromkeys(("public", *shells))),
        "ANGEE_GRAPHQL_IDE": graphql_ide
        if graphql_ide is not None
        else ("graphiql" if debug else None),
        "REBAC_BACKEND": "local",
        "REBAC_LOCAL_BACKEND_STORAGE": "registry",
        "REBAC_FIELD_READ_MODE": "redact",
        "REBAC_ALLOW_SUDO": True,
        "MIGRATION_MODULES": {
            **dict(migration_modules or {}),
            **_migration_modules(addons, runtime_module),
        },
        "STATIC_URL": static_url,
    }


def _resolve_data_dir(data_dir: Path | None) -> Path:
    """Return the explicit data directory or fail before Django opens state."""

    value = os.environ.get("ANGEE_DATA_DIR")
    if value:
        return Path(value)
    if data_dir is not None:
        return Path(data_dir)
    raise ImproperlyConfigured(
        "ANGEE_DATA_DIR must be set before importing host settings"
    )


def _addon_entries(addons: Sequence[str]) -> list[str]:
    """Resolve addon packages to AppConfig dotted paths."""

    entries: list[str] = []
    for addon in addons:
        config_class = _resolve_app_config_class(addon)
        if config_class is BaseConfig:
            continue
        entries.append(f"{config_class.__module__}.{config_class.__name__}")
    return entries


def _migration_modules(
    addons: Sequence[str],
    runtime_module: str,
) -> dict[str, str]:
    """Return migration-module overrides for emitted apps."""

    labels = {BaseConfig.label}
    for addon in addons:
        labels.add(_resolve_app_config_class(addon).label)
    return {
        label: f"{runtime_module}.{label}.migrations"
        for label in sorted(labels)
    }


def _resolve_app_config_class(package_name: str) -> type[BaseAddonConfig]:
    """Return the Django-selected addon config class for a package."""

    if package_name in {BaseConfig.name, BaseConfig.label}:
        return BaseConfig
    app_config = AppConfig.create(package_name)
    if not isinstance(app_config, BaseAddonConfig):
        raise ImproperlyConfigured(
            f"{package_name} must resolve to a BaseAddonConfig subclass"
        )
    return type(app_config)


def _dedupe(values: Sequence[str]) -> list[str]:
    """Return values in first-seen order without duplicates."""

    return list(dict.fromkeys(values))
