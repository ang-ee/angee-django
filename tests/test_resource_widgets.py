"""Focused contracts for resource xref parsing and import diff values."""

from __future__ import annotations

import pytest

from angee.resources.widgets import XrefManyToManyWidget, XrefWidgetMixin, _NativeJSONWidget, split_xref


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
