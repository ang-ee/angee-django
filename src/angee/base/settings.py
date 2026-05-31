"""Pure settings helper for composed Django hosts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseAddonConfig, BaseConfig

BASE_APP = "angee.base.apps.BaseConfig"
"""Installed app path for the Angee runtime base addon."""

RESOURCES_APP = "angee.resources.apps.ResourcesConfig"
"""Installed app path for the resource command host."""

COMPOSE_APP = "angee.compose.apps.ComposeConfig"
"""Installed app path for the build-time compose command host."""


def compose_defaults(
    *,
    addons: Sequence[str],
    runtime_dir: Path,
    data_dir: Path,
    root_urlconf: str,
    asgi_application: str,
    runtime_module: str = "runtime",
    debug: bool = False,
    use_tz: bool = True,
    graphql_ide: str | None = None,
    migration_modules: Mapping[str, str] | None = None,
    static_url: str = "/static/",
    extra_installed_apps: Sequence[str] = (),
    extra_middleware: Sequence[str] = (),
    channel_layers: Mapping[str, Any] | None = None,
    build: bool = False,
) -> dict[str, Any]:
    """Return Django settings for either build or runtime app sets."""

    addon_configs = _addon_config_classes(addons)
    installed_apps = (
        _build_installed_apps(addon_configs, extra_installed_apps)
        if build
        else _run_installed_apps(addon_configs, extra_installed_apps)
    )
    return {
        "INSTALLED_APPS": installed_apps,
        "MIDDLEWARE": [
            "django.middleware.common.CommonMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "rebac.middleware.ActorMiddleware",
            "simple_history.middleware.HistoryRequestMiddleware",
            "reversion.middleware.RevisionMiddleware",
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
        "ANGEE_GRAPHQL_IDE": graphql_ide
        if graphql_ide is not None
        else ("graphiql" if debug else None),
        "REBAC_BACKEND": "local",
        "REBAC_LOCAL_BACKEND_STORAGE": "registry",
        "REBAC_FIELD_READ_MODE": "redact",
        "REBAC_ALLOW_SUDO": True,
        "MIGRATION_MODULES": {
            **dict(migration_modules or {}),
            **_migration_modules((BaseConfig, *addon_configs), runtime_module),
        },
        "STATIC_URL": static_url,
    }


def _build_installed_apps(
    addon_configs: Sequence[type[BaseAddonConfig]],
    extra_installed_apps: Sequence[str],
) -> list[str]:
    """Return installed apps for the emit-only build process."""

    return _dedupe(
        [
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "rebac",
            "reversion",
            "simple_history",
            BASE_APP,
            *(f"{cls.__module__}.{cls.__name__}" for cls in addon_configs),
            COMPOSE_APP,
            *extra_installed_apps,
        ]
    )


def _run_installed_apps(
    addon_configs: Sequence[type[BaseAddonConfig]],
    extra_installed_apps: Sequence[str],
) -> list[str]:
    """Return installed apps for serving and post-build runtime commands."""

    return _dedupe(
        [
            "daphne",
            "channels",
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "rebac",
            "reversion",
            "simple_history",
            BASE_APP,
            RESOURCES_APP,
            *(f"{cls.__module__}.{cls.__name__}" for cls in addon_configs),
            *extra_installed_apps,
        ]
    )


def _addon_config_classes(
    addons: Sequence[str],
) -> tuple[type[BaseAddonConfig], ...]:
    """Resolve addon package names to Django-selected config classes."""

    classes: list[type[BaseAddonConfig]] = []
    for addon in addons:
        config_class = _resolve_app_config_class(addon)
        if config_class is BaseConfig:
            continue
        classes.append(config_class)
    return tuple(classes)


def _migration_modules(
    addon_configs: Sequence[type[BaseAddonConfig]],
    runtime_module: str,
) -> dict[str, str]:
    """Return migration module overrides for emitted runtime apps."""

    labels = {config_class.label for config_class in addon_configs}
    return {
        label: f"{runtime_module}.{label}.migrations"
        for label in sorted(labels)
    }


def _resolve_app_config_class(package_name: str) -> type[BaseAddonConfig]:
    """Return the Angee addon config class selected by Django."""

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
