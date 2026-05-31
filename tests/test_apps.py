"""Tests for base addon AppConfig contracts."""

from __future__ import annotations

from types import ModuleType
from typing import ClassVar

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

import angee.base
from angee.base.apps import BaseAddonConfig, BaseConfig
from angee.base.discovery import discover_addons


def _module(name: str) -> ModuleType:
    """Return a synthetic module with a Django app filesystem path."""

    module = ModuleType(name)
    module.__file__ = __file__
    return module


def test_base_addon_declares_no_source_models() -> None:
    """The base addon keeps source ownership outside its package."""

    assert apps.get_app_config("base").model_classes == ()


def test_resource_manifest_normalizes_tiers_and_entries() -> None:
    """Resource declarations normalize at the owning app config."""

    class ResourceConfig(BaseAddonConfig):
        name = "tests.resources"
        label = "test_resources"
        resources: ClassVar[dict[object, object]] = {
            "install": (
                "resources/users.csv",
                {
                    "path": "resources/notes.yaml",
                    "depends_on": ["resources/users.csv"],
                    "adopt": True,
                },
                {
                    "path": "resources/comments.yaml",
                    "depends_on": "resources/notes.yaml",
                },
            ),
            "demo": {"url": "https://example.test/demo.csv"},
        }

    config = ResourceConfig("tests.resources", _module("tests.resources"))

    assert config.resource_manifest["master"] == ()
    assert config.resource_manifest["install"] == (
        {"path": "resources/users.csv"},
        {
            "path": "resources/notes.yaml",
            "depends_on": ("resources/users.csv",),
            "adopt": True,
        },
        {
            "path": "resources/comments.yaml",
            "depends_on": ("resources/notes.yaml",),
        },
    )
    assert config.resource_manifest["demo"] == (
        {"url": "https://example.test/demo.csv"},
    )


def test_resource_manifest_rejects_unknown_tiers() -> None:
    """Only resource tiers owned by the framework are accepted."""

    class BrokenConfig(BaseAddonConfig):
        name = "tests.broken_resources"
        label = "broken_resources"
        resources: ClassVar[dict[object, object]] = {
            "fixture": ("resources/fixture.csv",)
        }

    config = BrokenConfig(
        "tests.broken_resources",
        _module("tests.broken_resources"),
    )

    with pytest.raises(ImproperlyConfigured, match="Unknown resource tier"):
        config.resource_manifest


def _config_with_schemas(schemas: object) -> BaseConfig:
    """Return a base config whose graphql module exports ``schemas``."""

    config = BaseConfig("angee.base", angee.base)
    module = ModuleType("fake.graphql")
    module.schemas = schemas  # type: ignore[attr-defined]
    config.__dict__["graphql_module"] = module
    return config


def test_get_schema_parts_normalizes_scalars_and_buckets() -> None:
    """A scalar contribution becomes a one-tuple; absent buckets are empty."""

    sentinel = object()
    config = _config_with_schemas({"public": {"query": sentinel}})
    parts = config.schema_parts

    assert parts["public"]["query"] == (sentinel,)
    assert parts["public"]["mutation"] == ()


def test_get_schema_parts_rejects_unknown_keys() -> None:
    """An unknown merge bucket fails fast."""

    config = _config_with_schemas({"public": {"queries": []}})
    with pytest.raises(ImproperlyConfigured, match="unknown keys: queries"):
        config.schema_parts


def test_get_schema_parts_rejects_sets() -> None:
    """Unordered sets are rejected so builds stay deterministic."""

    config = _config_with_schemas({"public": {"query": {object()}}})
    with pytest.raises(ImproperlyConfigured, match="not a set"):
        config.schema_parts


def test_get_schema_parts_missing_module_is_empty() -> None:
    """An addon without a graphql module contributes nothing."""

    config = BaseConfig("angee.base", angee.base)
    config.__dict__["graphql_module"] = None

    assert config.schema_parts == {}


def test_discover_addons_orders_dependencies() -> None:
    """Discovery returns addon configs after their dependencies."""

    class FirstConfig(BaseAddonConfig):
        name = "tests.first"
        label = "first"

    class SecondConfig(BaseAddonConfig):
        name = "tests.second"
        label = "second"
        depends_on: ClassVar[tuple[str, ...]] = ("first",)

    first = FirstConfig("tests.first", _module("tests.first"))
    second = SecondConfig("tests.second", _module("tests.second"))

    class Registry:
        """Small registry exposing app configs for discovery."""

        def get_app_configs(self) -> tuple[BaseAddonConfig, ...]:
            """Return configs in intentionally unsorted order."""

            return (second, first)

    assert discover_addons(Registry()) == (first, second)  # type: ignore[arg-type]


def test_discover_addons_rejects_cycles() -> None:
    """Dependency cycles fail through discovery."""

    class FirstConfig(BaseAddonConfig):
        name = "tests.cycle_first"
        label = "cycle_first"
        depends_on: ClassVar[tuple[str, ...]] = ("cycle_second",)

    class SecondConfig(BaseAddonConfig):
        name = "tests.cycle_second"
        label = "cycle_second"
        depends_on: ClassVar[tuple[str, ...]] = ("cycle_first",)

    first = FirstConfig("tests.cycle_first", _module("tests.cycle_first"))
    second = SecondConfig(
        "tests.cycle_second",
        _module("tests.cycle_second"),
    )

    class Registry:
        """Small registry exposing app configs for discovery."""

        def get_app_configs(self) -> tuple[BaseAddonConfig, ...]:
            """Return cyclic configs."""

            return (first, second)

    with pytest.raises(ImproperlyConfigured, match="Cycle"):
        discover_addons(Registry())  # type: ignore[arg-type]
