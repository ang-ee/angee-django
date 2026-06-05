"""Autoconfig loading plus settings fragments required by the composer app."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from types import ModuleType
from typing import Any

import django_yamlconf
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

RESERVED_AUTOCONFIG_SETTINGS = frozenset(
    {
        "ANGEE_RUNTIME_DIR",
        "ANGEE_RUNTIME_MODULE",
        "ASGI_APPLICATION",
        "INSTALLED_APPS",
        "MIGRATION_MODULES",
        "ROOT_URLCONF",
    }
)
YAMLCONF_ATTRIBUTES = "_YAMLCONF_ATTRIBUTES"
YAMLCONF_INTERNAL_SOURCE = "**INTERNAL**"
YAMLCONF_ENVIRONMENT_SOURCE = "**ENVIRONMENT**"


class _YamlconfErrorHandler(logging.Handler):
    """Turn yamlconf logged errors into composition failures."""

    def emit(self, record: logging.LogRecord) -> None:
        """Raise for every yamlconf error record."""

        raise ImproperlyConfigured(record.getMessage())


@contextmanager
def fail_on_yamlconf_errors() -> Iterator[None]:
    """Raise ``ImproperlyConfigured`` when django-yamlconf logs an error."""

    logger = logging.getLogger("django_yamlconf")
    handler = _YamlconfErrorHandler(level=logging.ERROR)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


def setting_name(attribute_name: str) -> str:
    """Return the top-level Django setting name for one yamlconf attribute."""

    return attribute_name.split(":", maxsplit=1)[0].split(".", maxsplit=1)[0]


class AutoConfig:
    """Apply addon autoconfig modules to a settings namespace."""

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        """Store the settings namespace being mutated."""

        self.namespace = namespace

    def update_app(self, app_config: AppConfig) -> None:
        """Apply one app config's optional autoconfig module."""

        if not module_has_submodule(app_config.module, "autoconfig"):
            return
        module = importlib.import_module(f"{app_config.name}.autoconfig")
        contributed = getattr(module, "SETTINGS", {})
        if not isinstance(contributed, Mapping):
            raise ImproperlyConfigured(f"{app_config.name}.autoconfig.SETTINGS must be a mapping")

        attributes: dict[str, object] = {}
        for raw_key, value in contributed.items():
            key = str(raw_key)
            name = setting_name(key)
            if name in RESERVED_AUTOCONFIG_SETTINGS:
                raise ImproperlyConfigured(f"{app_config.name}.autoconfig must not define {name}")
            if ":" not in key and "." not in key and name in self.namespace:
                continue
            attributes[key] = value
        if not attributes:
            return

        settings_module = ModuleType("angee.compose.effective_settings")
        for key, value in self.namespace.items():
            setattr(settings_module, key, value)
        if not hasattr(settings_module, YAMLCONF_ATTRIBUTES):
            setattr(settings_module, YAMLCONF_ATTRIBUTES, {})

        with fail_on_yamlconf_errors():
            django_yamlconf.add_attributes(settings_module, attributes, app_config.name)

        names = {YAMLCONF_ATTRIBUTES} | {setting_name(key) for key in attributes}
        for name in names:
            if (name == YAMLCONF_ATTRIBUTES or (not name.startswith("_") and name.isupper())) and hasattr(
                settings_module, name
            ):
                self.namespace[str(name)] = getattr(settings_module, name)


SETTINGS = {
    "MIDDLEWARE:append": ["django.middleware.common.CommonMiddleware"],
}
"""Django settings contributed when the composer is installed."""
