"""Tests for Django app dependency resolution across addons and plain apps."""

from __future__ import annotations

from angee.addons import addon_manifest
from angee.compose.appgraph import AppGraph
from tests.conftest import make_addon


def test_folder_addon_dependency_resolves_manifest_free_core_app() -> None:
    """An addon may depend on ``angee.base`` after core stops being an addon."""

    consumer = make_addon(name="example.consumer", depends_on=("angee.base",))

    resolved = AppGraph().resolve((consumer,))
    base, resolved_consumer = resolved

    assert base.name == "angee.base"
    assert addon_manifest(base) is None
    assert not hasattr(base, "angee_depends_on")
    assert base.angee_forced is True
    assert resolved_consumer is consumer
