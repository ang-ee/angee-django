"""Tests for Django app dependency resolution across addons and plain apps."""

from __future__ import annotations

from types import ModuleType

from django.apps import AppConfig

from angee.addons import AddonContract, addon_contract
from angee.compose.appgraph import AppGraph


def test_folder_addon_dependency_resolves_manifest_free_core_app() -> None:
    """An addon may depend on ``angee.base`` after core stops being an addon."""

    module = ModuleType("example.consumer")
    module.__file__ = __file__
    consumer = AppConfig("example.consumer", module)
    consumer._addon_contract = AddonContract(
        name="example.consumer",
        depends_on=("angee.base",),
    )

    resolved = AppGraph().resolve((consumer,))
    base, resolved_consumer = resolved

    assert base.name == "angee.base"
    assert addon_contract(base) is None
    assert base.angee_depends_on == ()
    assert base.angee_forced is True
    assert resolved_consumer is consumer
