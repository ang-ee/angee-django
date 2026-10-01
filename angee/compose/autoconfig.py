"""Autoconfig loading plus settings fragments required by the composer app."""

from __future__ import annotations

import copy
import importlib
import os
from collections import ChainMap
from collections.abc import Mapping, MutableMapping
from types import ModuleType
from typing import Any

import django_yamlconf
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

from angee.compose.yamlconf import (
    YAMLCONF_ATTRIBUTES,
    YAMLCONF_ENVIRONMENT_SOURCE,
    fail_on_yamlconf_errors,
    is_setting_name,
    setting_name,
)

COMPOSER_OWNED_SETTINGS = frozenset({"ANGEE_RUNTIME_DIR", "ASGI_APPLICATION", "INSTALLED_APPS", "ROOT_URLCONF"})


class AutoConfig:
    """Apply optional app autoconfig modules to a settings namespace."""

    def __init__(
        self,
        namespace: MutableMapping[str, Any],
        *,
        reserved_settings: frozenset[str] = COMPOSER_OWNED_SETTINGS,
        environment: bool = True,
    ) -> None:
        """Store the settings namespace being mutated."""

        self.namespace = namespace
        self.reserved_settings = reserved_settings
        self.environment = os.environ if environment else {}
        self.settings_module = ModuleType("angee.compose.effective_settings")
        self.settings_module.__dict__.update(namespace)
        self.settings_module.__dict__.setdefault(YAMLCONF_ATTRIBUTES, {})

    @classmethod
    def apply_installed(
        cls,
        namespace: MutableMapping[str, Any],
        *,
        environment: bool = True,
    ) -> None:
        """Apply autoconfig for an already ordered ``INSTALLED_APPS`` declaration.

        Bare settings call this before Django populates its registry. Django's
        factory resolves app paths and explicit config paths; existing config
        instances are reused. This does not expand dependencies, import models,
        run ready hooks, or generate a runtime. Set ``environment=False`` for
        isolated settings: neither declared overrides nor derived settings then
        read the process environment.
        """

        autoconfig = cls(namespace, environment=environment)
        for entry in namespace["INSTALLED_APPS"]:
            app_config = entry if isinstance(entry, AppConfig) else AppConfig.create(entry)
            autoconfig.update_app(app_config)

    def update_app(self, app_config: AppConfig) -> None:
        """Apply one app config's optional autoconfig module."""

        if not module_has_submodule(app_config.module, "autoconfig"):
            return
        module = importlib.import_module(f"{app_config.name}.autoconfig")
        declared, derived = self._module_settings(module, app_config)
        contributed = {**declared, **derived}

        attributes: dict[str, object] = {}
        declared_names: set[str] = set()
        for raw_key, value in contributed.items():
            key = str(raw_key)
            name = setting_name(key)
            declared_names.add(name)
            if name in self.reserved_settings:
                raise ImproperlyConfigured(f"{app_config.name}.autoconfig must not define {name}")
            if ":" not in key and "." not in key and name in self.namespace and not (
                name in derived and name in self.environment
            ):
                continue
            attributes[key] = copy.deepcopy(value)
        env_attributes = {
            name: self.environment[name]
            for name in sorted(declared_names)
            if name.startswith("ANGEE_") and name not in self.reserved_settings and name in self.environment
        }
        if not attributes and not env_attributes:
            return

        settings_module = self.settings_module

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

    def _module_settings(
        self, module: ModuleType, app_config: AppConfig,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        """Return defaults and derived values, with environment input taking precedence.

        Keep derived values distinct so an environment-backed value can replace
        a project value without bypassing its hook's parsing or changing the
        precedence of unrelated static defaults.
        """

        contributed = getattr(module, "SETTINGS", {})
        if not isinstance(contributed, Mapping):
            raise ImproperlyConfigured(f"{app_config.name}.autoconfig.SETTINGS must be a mapping")
        derive = getattr(module, "settings", None)
        if derive is None:
            return contributed, {}
        if not callable(derive):
            raise ImproperlyConfigured(f"{app_config.name}.autoconfig.settings must be callable")
        derived = derive(ChainMap(self.environment, self.namespace))
        if not isinstance(derived, Mapping):
            raise ImproperlyConfigured(f"{app_config.name}.autoconfig.settings() must return a mapping")
        return contributed, derived


SETTINGS = {
    "MIDDLEWARE:append": ["django.middleware.common.CommonMiddleware"],
}
"""Django settings contributed when the composer is installed."""
