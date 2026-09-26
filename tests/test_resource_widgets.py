"""Focused contracts for resource xref parsing and import diff values."""

from __future__ import annotations

import pytest

from angee.resources.widgets import _NativeJSONWidget, split_xref


def test_split_xref_prefers_the_longest_installed_addon_alias() -> None:
    """Dotted addon names and dotted local keys remain unambiguous."""

    aliases = {
        "angee": "angee",
        "angee.workflows_extraction": "angee.workflows_extraction",
        "workflows_extraction": "angee.workflows_extraction",
    }

    assert split_xref("angee.workflows_extraction.document.pipeline", aliases) == (
        "angee.workflows_extraction",
        "document.pipeline",
    )
    assert split_xref("workflows_extraction.document.pipeline", aliases) == (
        "angee.workflows_extraction",
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
