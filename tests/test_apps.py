"""Tests for base addon AppConfig contracts."""

from __future__ import annotations

from types import ModuleType

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

import angee.base
from angee.base.apps import BaseConfig
from angee.base.resources.models import Resource


def test_base_addon_owns_resource_model() -> None:
    """The Resource ledger is a base addon source model."""

    assert Resource in apps.get_app_config("base").model_classes


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
