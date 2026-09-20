"""Focused regressions for model-owned workflow resource installation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import tablib
from django.apps import AppConfig
from rebac import system_context

from angee.resources.entries import ResourceEntry, ResourceGroup
from angee.resources.loader import AngeeResource, build_resource
from angee.resources.models import Resource
from angee.workflows.definitions import (
    DefinitionEdit,
    DefinitionResult,
    EdgePatch,
    EndpointRef,
    NodePatch,
)
from angee.workflows.resource_install import import_resource_groups
from tests.test_workflows_resources import WorkflowResourceLedger
from tests.test_workflows_resources import workflow_resource_tables as _workflow_resource_tables  # noqa: F401
from tests.workflows import Edge, Step, Workflow

_OMITTED = object()
pytestmark = pytest.mark.usefixtures("_workflow_resource_tables")


def _addon(tmp_path: Path) -> AppConfig:
    module = ModuleType("tests.resource_install_probe")
    module.__file__ = str(tmp_path / "resource_install_probe" / "__init__.py")
    return AppConfig(module.__name__, module)


def _group(
    addon: AppConfig,
    model: type[Any],
    source: str,
    rows: tuple[Mapping[str, Any], ...],
) -> tuple[ResourceGroup, AngeeResource]:
    headers = ["_xref", *sorted({name for row in rows for name in row if name != "_xref"})]
    dataset = tablib.Dataset(headers=headers)
    for row in rows:
        dataset.append([row.get(header) for header in headers])
    entry = ResourceEntry(
        addon=addon,
        tier=Resource.Tier.INSTALL,
        source_value=source,
        model=model._meta.label,
    )
    return (
        ResourceGroup(entry, model._meta.label, dataset, list(range(1, len(rows) + 1))),
        build_resource(
            model,
            entry,
            ledger_model=WorkflowResourceLedger,
            addon_aliases={addon.label: addon.name},
        ),
    )


def _groups(
    addon: AppConfig,
    *,
    entry_name: str = "Entry",
    entry_config: object = _OMITTED,
    entry_binding: object = _OMITTED,
    route_source: str = "entry",
    route_target: str = "alpha",
) -> tuple[tuple[ResourceGroup, AngeeResource], ...]:
    handle = addon.label
    entry: dict[str, Any] = {
        "_xref": "entry",
        "workflow": f"{handle}.lineage",
        "key": "entry",
        "name": entry_name,
        "step_class": "fixture",
        "is_entry": True,
    }
    if entry_config is not _OMITTED:
        entry["config"] = entry_config
    if entry_binding is not _OMITTED:
        entry["input_binding"] = entry_binding
    steps = (
        entry,
        {
            "_xref": "alpha",
            "workflow": f"{handle}.lineage",
            "key": "alpha",
            "name": "Alpha",
            "step_class": "fixture",
        },
        {
            "_xref": "beta",
            "workflow": f"{handle}.lineage",
            "key": "beta",
            "name": "Beta",
            "step_class": "fixture",
        },
        {
            "_xref": "final",
            "workflow": f"{handle}.lineage",
            "key": "final",
            "name": "Final",
            "step_class": "fixture",
        },
    )
    edges = (
        {
            "_xref": "route",
            "workflow": f"{handle}.lineage",
            "source": f"{handle}.{route_source}",
            "target": f"{handle}.{route_target}",
        },
        {
            "_xref": "entry_beta",
            "workflow": f"{handle}.lineage",
            "source": f"{handle}.entry",
            "target": f"{handle}.beta",
        },
        {
            "_xref": "alpha_final",
            "workflow": f"{handle}.lineage",
            "source": f"{handle}.alpha",
            "target": f"{handle}.final",
        },
        {
            "_xref": "beta_final",
            "workflow": f"{handle}.lineage",
            "source": f"{handle}.beta",
            "target": f"{handle}.final",
        },
    )
    return (
        _group(
            addon,
            Workflow,
            "resources/install/100_workflows.workflow.yaml",
            ({"_xref": "lineage", "key": "resource-install-probe", "name": "Resource install probe"},),
        ),
        _group(addon, Step, "resources/install/101_workflows.step.yaml", steps),
        _group(addon, Edge, "resources/install/102_workflows.edge.yaml", edges),
    )


def _install(addon: AppConfig, **changes: Any) -> None:
    with system_context(reason="test workflow resource install"):
        import_resource_groups(
            Workflow,
            _groups(addon, **changes),
            ledger_model=WorkflowResourceLedger,
            addon_aliases={addon.label: addon.name},
        )


def _capture_edits(
    monkeypatch: pytest.MonkeyPatch,
    *,
    passthrough: bool,
) -> list[DefinitionEdit]:
    native_apply = Workflow.objects.apply_definition
    edits: list[DefinitionEdit] = []

    def capture(workflow: Workflow, *, expected_revision: int, edit: DefinitionEdit) -> DefinitionResult:
        edits.append(edit)
        if passthrough:
            return native_apply(workflow, expected_revision=expected_revision, edit=edit)
        return DefinitionResult(expected_revision, (), (), ())

    monkeypatch.setattr(Workflow.objects, "apply_definition", capture)
    return edits


def test_installer_ignores_reordered_json_object_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Widget rendering makes JSON object key order irrelevant to patches."""

    addon = _addon(tmp_path)
    _install(addon, entry_config={"policy": {"first": 1, "second": 2}})
    workflow = Workflow.objects.get(key="resource-install-probe")
    revision = workflow.draft_revision
    edits = _capture_edits(monkeypatch, passthrough=False)

    _install(addon, entry_config={"policy": {"second": 2, "first": 1}})

    workflow.refresh_from_db()
    assert edits == []
    assert workflow.draft_revision == revision


def test_installer_distinguishes_empty_object_false_and_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native JSON false and null do not compare equal to an empty object."""

    addon = _addon(tmp_path)
    _install(addon, entry_binding={})
    entry = Step.objects.get(key="entry")
    edits = _capture_edits(monkeypatch, passthrough=False)

    _install(addon, entry_binding=False)

    assert edits == [DefinitionEdit(node_patches=(NodePatch(entry.pk, {"input_binding": False}),))]
    edits.clear()

    _install(addon, entry_binding=None)

    assert edits == [DefinitionEdit(node_patches=(NodePatch(entry.pk, {"input_binding": None}),))]


def test_installer_retains_omitted_same_impl_config_and_patches_only_changed_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted config stays intact while one declared field changes alone."""

    addon = _addon(tmp_path)
    config = {"operator_policy": {"first": 1, "second": 2}}
    _install(addon, entry_config=config)
    entry = Step.objects.get(key="entry")
    edits = _capture_edits(monkeypatch, passthrough=True)

    _install(addon, entry_name="Renamed entry")

    entry.refresh_from_db()
    assert edits == [DefinitionEdit(node_patches=(NodePatch(entry.pk, {"name": "Renamed entry"}),))]
    assert entry.config == config


def test_installer_emits_refs_for_edge_endpoint_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint move carries both resolved references in its edge patch."""

    addon = _addon(tmp_path)
    _install(addon)
    edge = Edge.objects.get(source__key="entry", target__key="alpha")
    alpha = Step.objects.get(key="alpha")
    beta = Step.objects.get(key="beta")
    edits = _capture_edits(monkeypatch, passthrough=True)

    _install(addon, route_source="beta", route_target="alpha")

    assert edits == [
        DefinitionEdit(
            edge_patches=(
                EdgePatch(
                    edge.pk,
                    {},
                    source=EndpointRef(existing_id=beta.pk),
                    target=EndpointRef(existing_id=alpha.pk),
                ),
            )
        )
    ]
