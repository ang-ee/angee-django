"""Settings helper for small composed Django hosts."""

from __future__ import annotations

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
) -> dict[str, Any]:
    """Return Django setting values derived from Angee composition.

    ``runtime_dir`` and ``data_dir`` are explicit inputs. The host settings
    module decides them, resolving any ``ANGEE_RUNTIME_DIR`` or
    ``ANGEE_DATA_DIR`` override itself, and passes the result. This helper is a
    pure function of its arguments: it does not read the environment or mutate
    global state. The host owns making the generated ``runtime`` package
    importable (putting ``runtime_dir.parent`` on ``sys.path``).
    """

    addon_configs = _addon_config_classes(addons)
    return {
        "INSTALLED_APPS": _dedupe(
            [
                "daphne",
                "channels",
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "rebac",
                "reversion",
                "simple_history",
                f"{BaseConfig.__module__}.{BaseConfig.__name__}",
                *(f"{cls.__module__}.{cls.__name__}" for cls in addon_configs),
                *extra_installed_apps,
            ]
        ),
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


def _addon_config_classes(
    addons: Sequence[str],
) -> list[type[BaseAddonConfig]]:
    """Resolve addon packages to their config classes once, in order.

    ``BaseConfig`` is excluded because the base addon is installed explicitly.
    Resolving here keeps ``AppConfig.create()`` from running twice per addon.
    """

    classes: list[type[BaseAddonConfig]] = []
    for addon in addons:
        config_class = _resolve_app_config_class(addon)
        if config_class is BaseConfig:
            continue
        classes.append(config_class)
    return classes


def _migration_modules(
    addon_configs: Sequence[type[BaseAddonConfig]],
    runtime_module: str,
) -> dict[str, str]:
    """Return migration-module overrides for emitted apps."""

    labels = {config_class.label for config_class in addon_configs}
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
