"""Autoconfig loading plus settings fragments required by the composer app."""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from types import ModuleType
from typing import Any

import django_yamlconf
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

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


def is_setting_name(name: str) -> bool:
    """Return whether ``name`` is a top-level Django setting (public, all-caps).

    The one owner of the rule that decides which namespace entries the composer
    treats as Django settings. The ``YAMLCONF_ATTRIBUTES`` provenance sentinel
    is exported alongside settings but is not itself a setting name, so callers
    that carry it forward OR it in explicitly.
    """

    return not name.startswith("_") and name.isupper()


class AutoConfig:
    """Apply addon autoconfig modules to a settings namespace."""

    def __init__(self, namespace: MutableMapping[str, Any], *, reserved_settings: frozenset[str]) -> None:
        """Store the settings namespace being mutated."""

        self.namespace = namespace
        self.reserved_settings = reserved_settings

    def update_app(self, app_config: AppConfig) -> None:
        """Apply one app config's optional autoconfig module."""

        if not module_has_submodule(app_config.module, "autoconfig"):
            return
        module = importlib.import_module(f"{app_config.name}.autoconfig")
        contributed = getattr(module, "SETTINGS", {})
        if not isinstance(contributed, Mapping):
            raise ImproperlyConfigured(f"{app_config.name}.autoconfig.SETTINGS must be a mapping")

        attributes: dict[str, object] = {}
        declared_names: set[str] = set()
        for raw_key, value in contributed.items():
            key = str(raw_key)
            name = setting_name(key)
            declared_names.add(name)
            if name in self.reserved_settings:
                raise ImproperlyConfigured(f"{app_config.name}.autoconfig must not define {name}")
            if ":" not in key and "." not in key and name in self.namespace:
                continue
            attributes[key] = value
        env_attributes = {
            name: os.environ[name]
            for name in sorted(declared_names)
            if name.startswith("ANGEE_") and name not in self.reserved_settings and name in os.environ
        }
        if not attributes and not env_attributes:
            return

        settings_module = ModuleType("angee.compose.effective_settings")
        for key, value in self.namespace.items():
            setattr(settings_module, key, value)
        if not hasattr(settings_module, YAMLCONF_ATTRIBUTES):
            setattr(settings_module, YAMLCONF_ATTRIBUTES, {})

        with fail_on_yamlconf_errors():
            if attributes:
                django_yamlconf.add_attributes(settings_module, attributes, app_config.name)
            if env_attributes:
                django_yamlconf.add_attributes(
                    settings_module,
                    env_attributes,
                    YAMLCONF_ENVIRONMENT_SOURCE,
                )

        names = {YAMLCONF_ATTRIBUTES} | {setting_name(key) for key in attributes} | set(env_attributes)
        for name in names:
            if (name == YAMLCONF_ATTRIBUTES or is_setting_name(name)) and hasattr(settings_module, name):
                self.namespace[str(name)] = getattr(settings_module, name)


SETTINGS = {
    "MIDDLEWARE:append": ["django.middleware.common.CommonMiddleware"],
}
"""Django settings contributed when the composer is installed."""
