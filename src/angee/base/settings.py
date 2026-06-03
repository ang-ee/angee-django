"""Pure settings helper for composed Django hosts."""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

from angee.base.apps import BaseAddonConfig, BaseConfig

BASE_APP = "angee.base.apps.BaseConfig"
"""Installed app path for the Angee runtime base addon."""

RESOURCES_APP = "angee.resources.apps.ResourcesConfig"
"""Installed app path for the resource command host."""

COMPOSE_APP = "angee.compose.apps.ComposeConfig"
"""Installed app path for the build-time compose command host."""

IAM_ADDON = "angee.iam"
"""Built-in IAM addon package installed for every composed host."""


def compose_defaults(
    *,
    addons: Sequence[str],
    runtime_dir: Path,
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
    """Return Django settings for one composed host.

    There is a single app set and a single boot. The composer emits the
    concrete runtime in ``import_models`` (app-populate phase 2) *before* the
    addons adopt it, so by phase 3 ``iam.User`` is registered and Django's auth
    contract resolves normally. No build/run mode, no ``ANGEE_BUILD`` flag.
    """

    addon_configs = _addon_config_classes(_with_builtin_addons(addons))
    composed: dict[str, Any] = {
        "INSTALLED_APPS": _installed_apps(addon_configs, extra_installed_apps),
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
        "CHANNEL_LAYERS": channel_layers or {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        "ROOT_URLCONF": root_urlconf,
        "ASGI_APPLICATION": asgi_application,
        "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
        "USE_TZ": use_tz,
        "ANGEE_RUNTIME_DIR": runtime_dir,
        "ANGEE_RUNTIME_MODULE": runtime_module,
        "ANGEE_GRAPHQL_IDE": graphql_ide if graphql_ide is not None else ("graphiql" if debug else None),
        "REBAC_BACKEND": "local",
        "REBAC_LOCAL_BACKEND_STORAGE": "registry",
        "REBAC_STRICT_MODE": True,
        "REBAC_FIELD_READ_MODE": "redact",
        "REBAC_ALLOW_SUDO": True,
        # Admin reach is expressed in the schema (const-backed `admin` relations
        # → angee/role:admin), so superusers go through REBAC like everyone else
        # rather than a queryset-layer bypass. The PermissionsMixin still
        # short-circuits Django-admin `has_perm` at the object level.
        "REBAC_SUPERUSER_BYPASS": False,
        "MIGRATION_MODULES": {
            **dict(migration_modules or {}),
            **_migration_modules((BaseConfig, *addon_configs), runtime_module),
        },
        "STATIC_URL": static_url,
    }
    # Addons contribute setting defaults (e.g. IAM's AUTH_USER_MODEL) beneath
    # framework defaults and host overrides.
    for key, value in _addon_settings_defaults(addon_configs).items():
        composed.setdefault(key, value)
    return composed


def _installed_apps(
    addon_configs: Sequence[type[BaseAddonConfig]],
    extra_installed_apps: Sequence[str],
) -> list[str]:
    """Return the single composed app set in deterministic, adopt-safe order.

    ``COMPOSE_APP`` is listed before ``BASE_APP`` and the source addons so its
    ``import_models`` emits the concrete runtime in phase 2 before any addon
    adopts ``runtime.<label>`` in the same phase.
    """

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
            COMPOSE_APP,
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


def _with_builtin_addons(addons: Sequence[str]) -> tuple[str, ...]:
    """Return user addons with framework-owned addons appended once."""

    normalized = tuple(addons)
    if IAM_ADDON in normalized or "iam" in normalized:
        return normalized
    return (*normalized, IAM_ADDON)


def _migration_modules(
    addon_configs: Sequence[type[BaseAddonConfig]],
    runtime_module: str,
) -> dict[str, str]:
    """Return migration module overrides for emitted runtime apps.

    Only addons whose package ships a ``models`` module get a redirected
    migration namespace, because the composer emits ``runtime/<label>/`` (and its
    migration package) only for label-owning model sources. A model-less addon —
    e.g. a pure GraphQL/REBAC bridge with no ``models.py`` — has no runtime
    package, so mapping it would point Django's migration loader at a missing
    ``runtime.<label>.migrations``.

    The presence check is a settings-time-safe *heuristic*: the true owner is the
    composer's emitted label set (``AngeeRuntime.sources_by_label``, derived from
    owned ``model_classes``), but resolving that needs models imported, which is
    unsafe before app population. The heuristic is exact for an addon that either
    has no ``models.py`` or has one declaring owned source models; only an
    *empty* or *extension-only* ``models.py`` would be mis-mapped. Closing that
    gap means the composer emitting an empty migration package for every composed
    addon (it already holds the full ``self.addons`` list) so this can map every
    label — a tracked follow-up, not needed for any current addon.
    """

    labels = {
        config_class.label for config_class in addon_configs if _emits_runtime_models(config_class)
    }
    return {label: f"{runtime_module}.{label}.migrations" for label in sorted(labels)}


def _emits_runtime_models(config_class: type[BaseAddonConfig]) -> bool:
    """Return whether the addon's package ships a ``models`` module.

    A settings-time-safe heuristic for "the composer emits a runtime app for this
    label" (see ``_migration_modules``); checks file presence without importing,
    since models cannot be imported before app population.
    """

    package = importlib.import_module(config_class.name)
    return module_has_submodule(package, "models")


def _addon_settings_defaults(
    addon_configs: Sequence[type[BaseAddonConfig]],
) -> dict[str, object]:
    """Return setting defaults contributed by addons.

    A key contributed by two addons with conflicting values is a composition
    error.
    """

    merged: dict[str, object] = {}
    owners: dict[str, str] = {}
    for config_class in addon_configs:
        for key, value in config_class.settings_defaults.items():
            owner = owners.get(key)
            if owner is not None and merged[key] != value:
                raise ImproperlyConfigured(
                    f"Addons {owner} and {config_class.__name__} both "
                    f"contribute setting {key!r} with conflicting values"
                )
            merged[key] = value
            owners[key] = config_class.__name__
    return merged


def _resolve_app_config_class(package_name: str) -> type[BaseAddonConfig]:
    """Return the Angee addon config class selected by Django."""

    if package_name in {BaseConfig.name, BaseConfig.label}:
        return BaseConfig
    app_config = AppConfig.create(package_name)
    if not isinstance(app_config, BaseAddonConfig):
        raise ImproperlyConfigured(f"{package_name} must resolve to a BaseAddonConfig subclass")
    return type(app_config)


def _dedupe(values: Sequence[str]) -> list[str]:
    """Return values in first-seen order without duplicates."""

    return list(dict.fromkeys(values))
