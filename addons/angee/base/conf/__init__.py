"""Angee's deployment overlays around the native django-compose settings owner."""
from __future__ import annotations

import os
from collections.abc import MutableMapping
from copy import deepcopy
from types import ModuleType
from typing import Any

import django_yamlconf
import environ
from django.core.exceptions import ImproperlyConfigured

from angee.base.conf import yamlconf
from angee.base.paths import resolve_path


def configure(namespace: MutableMapping[str, Any]) -> None:
    """Load Angee deployment inputs, then delegate composition once.

    The calling settings module owns the source directory. ANGEE_PROJECT_DIR
    may select a deployment directory; no ancestor configuration is searched.
    Python declarations, source YAML, deployment YAML, explicit final YAML and
    YAMLCONF environment definitions keep django-yamlconf's native precedence.
    """
    source = resolve_path(namespace["__file__"]).parent
    project = resolve_path(os.environ.get("ANGEE_PROJECT_DIR", source))
    env_file = project / ".env"
    env = environ.Env()
    namespace["COMPOSE_WATCH_PATHS"] = tuple(dict.fromkeys((
        *namespace.get("COMPOSE_WATCH_PATHS", ()), source / "settings.yaml", project / "settings.yaml", env_file,
    )))
    if env_file.is_file():
        env.read_env(env_file)
    target = ModuleType(str(namespace.get("__name__", "settings")))
    target.__dict__.update({
        name: deepcopy(value) if yamlconf.is_setting_name(name) and isinstance(value, dict) else value
        for name, value in namespace.items()
    })
    candidates = [source / "settings.yaml", project / "settings.yaml"]
    if final := os.environ.get("YAMLCONF_CONFFILE"):
        candidates.append(resolve_path(final))
    candidates = list(dict.fromkeys(path.resolve() for path in candidates))
    namespace["COMPOSE_WATCH_PATHS"] = tuple(dict.fromkeys((
        *namespace["COMPOSE_WATCH_PATHS"], *candidates,
    )))
    with yamlconf.fail_on_yamlconf_errors():
        attributes = django_yamlconf.bootstrap_attributes(str(project))
        django_yamlconf.add_attr_info(attributes, "SOURCE_DIR", str(source))
        bootstrap = frozenset(attributes) - {"BASE_DIR"}
        loader, kwargs = django_yamlconf.get_loader("yaml")
        if loader is not None:
            for path in candidates:
                if os.access(path, os.R_OK):
                    django_yamlconf.load_conffile(attributes, target, loader, kwargs, str(path))
        django_yamlconf.load_envdefs(attributes, target)
        django_yamlconf.expand_attribute_refs(attributes)
        django_yamlconf.inject_attr(attributes, target)
    namespace.update({
        name: value for name, value in vars(target).items()
        if name == yamlconf.YAMLCONF_ATTRIBUTES or yamlconf.is_setting_name(name) and name not in bootstrap
    })
    namespace.setdefault("BASE_DIR", project)
    # Native provenance includes dotted attributes and merge histories. Compiler
    # inputs cannot acquire deployment-specific identities through env overlays.
    for name in attributes:
        setting = yamlconf.setting_name(name)
        if (
            setting in {"INSTALLED_APPS", "DEFAULT_AUTO_FIELD", "AUTH_USER_MODEL", "MIGRATION_MODULES"}
            or setting.startswith("COMPOSE_") and setting != "COMPOSE_COMMAND_CONTEXT"
        ) and yamlconf.YAMLCONF_ENVIRONMENT_SOURCE in yamlconf.setting_sources(target, name):
            raise ImproperlyConfigured(f"Compiler input {setting} cannot be environment-sourced")
    namespace["ANGEE_PROJECT_YAML_SETTINGS"] = frozenset(
        name for name in django_yamlconf.defined_attributes(target)
        if django_yamlconf.get_attr_info(name, target).get("source") == str(project / "settings.yaml")
    )
    _deployment_urls(namespace, env)
    origins = namespace.get("CSRF_TRUSTED_ORIGINS", ())
    namespace["CSRF_TRUSTED_ORIGINS"] = [origins] if isinstance(origins, str) else list(origins)
    if origin := env.str("ANGEE_PUBLIC_ORIGIN", default=""):
        if origin not in namespace["CSRF_TRUSTED_ORIGINS"]:
            namespace["CSRF_TRUSTED_ORIGINS"].append(origin)
    namespace["COMPOSE_ADDON_GROUP"] = "angee.addons"
    namespace["COMPOSE_ENV_PREFIXES"] = ("ANGEE_",)
    namespace["COMPOSE_WATCH_PATHS"] = tuple(dict.fromkeys((
        *namespace.get("COMPOSE_WATCH_PATHS", ()), *candidates, env_file,
    )))
    from django_compose.conf import configure as compose_configure

    try:
        compose_configure(namespace)
    finally:
        _start_error_reporting(namespace, env)


def _deployment_urls(namespace: MutableMapping[str, Any], env: environ.Env) -> None:
    """Project URL environment inputs through django-environ's native parsers."""
    if "DATABASE_URL" in os.environ:
        database = env.db()
        if "postgresql" in database.get("ENGINE", ""):
            options = database.setdefault("OPTIONS", {})
            options.setdefault("options", "-c jit=off")
            if env.bool("ANGEE_DB_POOL", default=environ.Env.parse_value(namespace.get("ANGEE_DB_POOL", False), bool)):
                if database.get("CONN_MAX_AGE"):
                    raise ImproperlyConfigured("ANGEE_DB_POOL cannot be used with DATABASE_URL CONN_MAX_AGE")
                options["pool"] = {
                    "min_size": env.int(
                        "ANGEE_DB_POOL_MIN_SIZE", default=int(namespace.get("ANGEE_DB_POOL_MIN_SIZE", 0)),
                    ),
                    "max_size": env.int(
                        "ANGEE_DB_POOL_MAX_SIZE", default=int(namespace.get("ANGEE_DB_POOL_MAX_SIZE", 2)),
                    ),
                    "timeout": env.float(
                        "ANGEE_DB_POOL_TIMEOUT", default=float(namespace.get("ANGEE_DB_POOL_TIMEOUT", 5)),
                    ),
                }
        namespace.setdefault("DATABASES", {"default": database})
    if "CACHE_URL" in os.environ:
        namespace.setdefault("CACHES", {"default": env.cache()})
    if "CELERY_BROKER_URL" in os.environ:
        namespace.setdefault("CELERY_BROKER_URL", env.str("CELERY_BROKER_URL"))


def _start_error_reporting(namespace: MutableMapping[str, Any], env: environ.Env) -> None:
    """Start Sentry once after addon integration policy has been contributed."""
    import sentry_sdk

    if not env.str("SENTRY_DSN", default="") or sentry_sdk.is_initialized():
        return
    sentry_sdk.init(
        disabled_integrations=namespace.get("SENTRY_DISABLED_INTEGRATIONS", ()),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
    )
