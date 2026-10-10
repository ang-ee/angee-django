"""Focused contracts for resource xref parsing and import diff values."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from rebac import actor_context, system_context

from angee.resources.testing.models import Resource
from angee.resources.widgets import (
    XrefManyToManyWidget,
    XrefWidgetMixin,
    _NativeJSONWidget,
    resolve_ledger_xref,
    split_xref,
)
from tests.conftest import create_platform_admin


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("choice", ["anyOf", "oneOf"])
def test_config_resolution_uses_only_valid_relation_branches_and_preserves_the_source(choice):
    actor = create_platform_admin("config-reader")
    with system_context(reason="config reference fixture"):
        Resource.objects.bind_instance(
            addon=apps.get_app_config("iam"), xref="config_reader", instance=actor, source="test",
        )
    relation = {"type": "string", "relation": {"resource": actor._meta.label}}
    schema = {"type": "object", "properties": {"value": {choice: [
        {"type": "object", "properties": {"kind": {"const": "literal"}, "target": {"type": "string"}}},
        {"type": "object", "properties": {"kind": {"const": "reference"}, "target": relation}},
    ]}}}
    value = {"value": {"kind": "literal", "target": "iam.missing"}}
    with actor_context(actor):
        assert Resource.objects.resolve_config_references(value, schema=schema) == value
        value = {"value": {"kind": "reference", "target": "iam.config_reader"}}
        assert Resource.objects.resolve_config_references(value, schema=schema) == {
            "value": {"kind": "reference", "target": str(actor.sqid)},
        }
    assert value["value"]["target"] == "iam.config_reader"


def test_ledger_alias_collisions_fail_through_the_shared_resolution_owner(monkeypatch):
    monkeypatch.setattr(apps, "get_app_configs", lambda: (
        SimpleNamespace(name="example.first", label="shared"), SimpleNamespace(name="example.second", label="shared"),
    ))
    with pytest.raises(ImproperlyConfigured, match="Duplicate addon alias"):
        resolve_ledger_xref("shared.record")


def test_split_xref_prefers_the_longest_installed_addon_alias() -> None:
    """Dotted addon names and dotted local keys remain unambiguous."""

    aliases = {
        "example": "example",
        "example.documents": "example.documents",
        "documents": "example.documents",
    }

    assert split_xref("example.documents.document.pipeline", aliases) == (
        "example.documents",
        "document.pipeline",
    )
    assert split_xref("documents.document.pipeline", aliases) == (
        "example.documents",
        "document.pipeline",
    )
    with pytest.raises(ValueError, match="unresolved xref"):
        split_xref("missing.document.pipeline", aliases)


def test_native_json_widget_renders_semantic_values_canonically() -> None:
    """Import-export diffs ignore object key order without conflating falsey JSON."""

    widget = _NativeJSONWidget()

    assert widget.render({"second": 2, "first": 1}) == '{"first":1,"second":2}'
    assert widget.render({}) == "{}"
    assert widget.render(False) == "false"
    assert widget.render(None) is None


def test_relation_prerequisites_parse_fk_and_many_to_many_without_database_targets() -> None:
    aliases = {"iam": "angee.iam", "angee.iam": "angee.iam"}
    single = XrefWidgetMixin()
    single.addon_aliases = aliases
    assert single.referenced_handles("iam.deployment_operator") == frozenset({("angee.iam", "deployment_operator")})
    assert single.referenced_handles(None) == frozenset()
    multiple = XrefManyToManyWidget(model=None)
    multiple.addon_aliases = aliases
    assert multiple.referenced_handles("iam.first, angee.iam.second") == frozenset({
        ("angee.iam", "first"), ("angee.iam", "second"),
    })
    with pytest.raises(ValueError, match="xref"):
        single.referenced_handles(7)
