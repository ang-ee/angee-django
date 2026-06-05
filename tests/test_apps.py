"""Tests for plain Django AppConfig based Angee contracts."""

from __future__ import annotations

from types import ModuleType

import pytest
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseConfig
from angee.graphql.schema import schema_parts_for
from angee.resources.entries import resource_manifest_for


def _module(name: str) -> ModuleType:
    """Return a synthetic module with a Django app filesystem path."""

    module = ModuleType(name)
    module.__file__ = __file__
    return module


def test_base_config_is_a_dependency_node() -> None:
    """The model foundation participates in addon dependency ordering."""

    base = apps.get_app_config("base")

    assert isinstance(base, BaseConfig)
    assert base.depends_on == (
        "angee.compose",
        "django.contrib.contenttypes",
        "rebac",
        "reversion",
        "simple_history",
    )


def test_resource_manifest_normalizes_tiers_and_entries() -> None:
    """Resource declarations normalize in the resource subsystem."""

    class ResourceConfig(AppConfig):
        name = "tests.resources"
        label = "test_resources"
        resources = {
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
    manifest = resource_manifest_for(config)

    assert manifest["master"] == ()
    assert manifest["install"] == (
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
    assert manifest["demo"] == ({"url": "https://example.test/demo.csv"},)


def test_resource_manifest_rejects_unknown_tiers() -> None:
    """Only resource tiers owned by the resource subsystem are accepted."""

    class BrokenConfig(AppConfig):
        name = "tests.broken_resources"
        label = "broken_resources"
        resources = {"fixture": ("resources/fixture.csv",)}

    config = BrokenConfig(
        "tests.broken_resources",
        _module("tests.broken_resources"),
    )

    with pytest.raises(ImproperlyConfigured, match="Unknown resource tier"):
        resource_manifest_for(config)


def _config_with_schemas(schemas: object) -> AppConfig:
    """Return an app config whose ``schemas`` attribute carries a declaration."""

    config = AppConfig("tests.graphql", _module("tests.graphql"))
    config.schemas = schemas
    return config


def test_get_schema_parts_normalizes_scalars_and_buckets() -> None:
    """A scalar contribution becomes a one-tuple; absent buckets are empty."""

    sentinel = object()
    config = _config_with_schemas({"public": {"query": sentinel}})
    parts = schema_parts_for(config)

    assert parts["public"]["query"] == (sentinel,)
    assert parts["public"]["mutation"] == ()


def test_get_schema_parts_rejects_unknown_keys() -> None:
    """An unknown merge bucket fails fast."""

    config = _config_with_schemas({"public": {"queries": []}})
    with pytest.raises(ImproperlyConfigured, match="unknown keys: queries"):
        schema_parts_for(config)


def test_get_schema_parts_rejects_sets() -> None:
    """Unordered sets are rejected so builds stay deterministic."""

    config = _config_with_schemas({"public": {"query": {object()}}})
    with pytest.raises(ImproperlyConfigured, match="not a set"):
        schema_parts_for(config)


def test_get_schema_parts_missing_module_is_empty() -> None:
    """An addon without a schema module contributes nothing."""

    config = AppConfig("tests.no_schema", _module("tests.no_schema"))

    assert schema_parts_for(config) == {}


def test_config_attributes_are_owned_by_consumers() -> None:
    """Config attributes are declarations, not addon identity markers."""

    class ManifestOnlyConfig(AppConfig):
        name = "tests.manifest_only"
        label = "manifest_only"
        schemas = {}
        resources = {}
        url_patterns = "urls.urlpatterns"

    class DependencyNodeConfig(AppConfig):
        name = "tests.marked_addon"
        label = "marked_addon"
        depends_on = ()

    manifest_only = ManifestOnlyConfig(
        "tests.manifest_only",
        _module("tests.manifest_only"),
    )
    dependency_node = DependencyNodeConfig("tests.marked_addon", _module("tests.marked_addon"))

    assert manifest_only.schemas == {}
    assert manifest_only.resources == {}
    assert manifest_only.url_patterns == "urls.urlpatterns"
    assert dependency_node.depends_on == ()
