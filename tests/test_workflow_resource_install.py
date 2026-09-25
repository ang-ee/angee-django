"""Focused regressions for model-owned workflow resource installation."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import tablib
from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.db.models.fields import NOT_PROVIDED
from django.test.utils import CaptureQueriesContext
from import_export.results import RowResult
from pydantic import BaseModel, ConfigDict, Field
from rebac import system_context
from rebac.models import active_relationship_model
from rebac.resources import to_object_ref

from angee.base.identity import public_id_of
from angee.resources.entries import GrantGroup, GrantRow, LoadResult, ResourceEntry, ResourceGroup
from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import AngeeResource, build_resource
from angee.resources.models import Resource
from angee.workflows.definitions import (
    DefinitionEdit,
    DefinitionResult,
    EdgePatch,
    EndpointRef,
    NodePatch,
)
from angee.workflows.resources import WorkflowDefinitionResource
from tests.test_workflows_resources import WorkflowResourceLedger
from tests.test_workflows_resources import workflow_resource_tables as _workflow_resource_tables  # noqa: F401
from tests.workflows import Edge, FixtureStep, Step, Workflow

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
        dataset.append([row.get(header, NOT_PROVIDED) for header in headers])
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


def _load(groups: tuple[tuple[ResourceGroup, AngeeResource], ...], *, dry_run: bool = False) -> LoadResult:
    owners = {group.entry.addon.name: group.entry.addon for group, _ in groups}
    aliases = {key: owner.name for owner in owners.values() for key in (owner.label, owner.name)}
    return WorkflowResourceLedger.objects._import_groups(
        tuple(group.entry for group, _ in groups), tuple(group for group, _ in groups), (),
        dry_run=dry_run, addon_aliases=aliases,
    )


def _install(addon: AppConfig, **changes: Any) -> LoadResult:
    return _load(_groups(addon, **changes))


def _capture_edits(
    monkeypatch: pytest.MonkeyPatch,
    *,
    passthrough: bool,
) -> list[DefinitionEdit]:
    manager_type = type(Workflow.objects)
    native_apply = manager_type.apply_definition
    edits: list[DefinitionEdit] = []

    def capture(manager: Any, workflow: Workflow, *, expected_revision: int, edit: DefinitionEdit) -> DefinitionResult:
        edits.append(edit)
        if passthrough:
            return native_apply(manager, workflow, expected_revision=expected_revision, edit=edit)
        return DefinitionResult(expected_revision, (), (), ())

    monkeypatch.setattr(manager_type, "apply_definition", capture)
    return edits


def test_installer_ignores_reordered_json_object_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Widget rendering makes JSON object key order irrelevant to patches."""

    addon = _addon(tmp_path)
    _install(addon, entry_config={"policy": {"first": 1, "second": 2}})
    workflow = Workflow.system_queryset().get(key="resource-install-probe")
    revision = workflow.draft_revision
    edits = _capture_edits(monkeypatch, passthrough=False)

    _install(addon, entry_config={"policy": {"second": 2, "first": 1}})

    workflow.refresh_from_db()
    assert edits == []
    assert workflow.draft_revision == revision


def test_installer_distinguishes_empty_object_false_and_null(tmp_path: Path) -> None:
    """Native row validation rejects false; explicit nullable JSON remains null."""

    addon = _addon(tmp_path)
    _install(addon, entry_binding={})
    with pytest.raises(ResourceLoadError, match=r"101_workflows.step.yaml: 1:.*input_binding"):
        _install(addon, entry_binding=False)
    assert Step.system_queryset().get(key="entry").input_binding == {}
    _install(addon, entry_binding=None)
    assert Step.system_queryset().get(key="entry").input_binding is None


def test_installer_retains_omitted_same_impl_config_and_patches_only_changed_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted config stays intact while one declared field changes alone."""

    addon = _addon(tmp_path)
    config = {"operator_policy": {"first": 1, "second": 2}}
    _install(addon, entry_config=config)
    entry = Step.system_queryset().get(key="entry")
    edits = _capture_edits(monkeypatch, passthrough=True)

    _install(addon, entry_name="Renamed entry")

    entry.refresh_from_db()
    assert edits == [DefinitionEdit(node_patches=(NodePatch(entry.pk, {"name": "Renamed entry"}),))]
    assert entry.config == config


def test_installer_patches_declared_config_without_erasing_operator_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-owned config change retains omitted operator-owned policy."""

    addon = _addon(tmp_path)
    original = {
        "engine": {"prompt": "v1"},
        "operator_policy": {"reviewers": ["auth/user:reviewer"]},
    }
    _install(addon, entry_config=original)
    entry = Step.system_queryset().get(key="entry")
    edits = _capture_edits(monkeypatch, passthrough=True)

    _install(addon, entry_config={"engine": {"prompt": "v2"}})

    expected = {
        "engine": {"prompt": "v2"},
        "operator_policy": original["operator_policy"],
    }
    entry.refresh_from_db()
    assert edits == [DefinitionEdit(node_patches=(NodePatch(entry.pk, {"config": expected}),))]
    assert entry.config == expected


def test_installer_reports_retained_keys_the_config_contract_retired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retired keys are identified without exposing their operator-authored values."""

    class FixtureConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")

        profile: dict[str, Any] = Field(default_factory=dict)
        operator_policy: dict[str, Any] = Field(default_factory=dict)

    addon = _addon(tmp_path)
    _install(
        addon,
        entry_config={
            "engine": {"prompt": "private prompt"},
            "legacy_secret": "private secret",
            "operator_policy": {"reviewers": ["r"]},
        },
    )
    monkeypatch.setattr(FixtureStep, "config_model", FixtureConfig)

    with caplog.at_level(logging.WARNING, logger="angee.workflows.resources"):
        _install(addon, entry_config={"profile": {"prompt": "v2"}})

    entry = Step.system_queryset().get(key="entry")
    assert entry.config == {"profile": {"prompt": "v2"}, "operator_policy": {"reviewers": ["r"]}}
    warnings = [record for record in caplog.records if record.name == "angee.workflows.resources"]
    assert [(record.levelno, record.getMessage()) for record in warnings] == [
        (logging.WARNING, f"Dropping retired config key '{key}' from step 'entry' (xref={addon.name}.entry).")
        for key in ("engine", "legacy_secret")
    ]
    assert "private" not in caplog.text


def test_installer_emits_refs_for_edge_endpoint_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint move carries both resolved references in its edge patch."""

    addon = _addon(tmp_path)
    _install(addon)
    edge = Edge.system_queryset().get(source__key="entry", target__key="alpha")
    alpha = Step.system_queryset().get(key="alpha")
    beta = Step.system_queryset().get(key="beta")
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


@pytest.mark.parametrize("plan_first", [False, True])
def test_native_results_and_canonical_ledger_cover_create_update_skip_and_adoption(
    tmp_path: Path, plan_first: bool,
) -> None:
    addon = _addon(tmp_path)
    source = "resources/install/100_workflows.workflow.yaml"

    def imported(name: str) -> tuple[Any, Any]:
        group, resource = _group(addon, Workflow, source, ({"_xref": "head", "key": "head", "name": name},))
        with system_context(reason="native workflow import results"), transaction.atomic():
            if plan_first:
                AngeeResource.resolve_existing(((group.dataset, resource),))
            result = resource.import_data(group.dataset, raise_errors=True, use_transactions=False)
        return resource, result

    resource, created = imported("First")
    assert isinstance(resource, WorkflowDefinitionResource)
    assert created.totals[RowResult.IMPORT_TYPE_NEW] == 1
    first = created.rows[0]
    assert first.object_id == first.instance.pk
    assert first.object_repr == str(first.instance)
    ledger = WorkflowResourceLedger.objects.get(xref="head")
    assert ledger.target_id == public_id_of(first.instance)
    assert ledger.content_hash == resource._row_content_hash({"_xref": "head", "key": "head", "name": "First"})

    _, updated = imported("Second")
    assert updated.totals[RowResult.IMPORT_TYPE_UPDATE] == 1
    assert updated.rows[0].object_id == first.object_id
    ledger.refresh_from_db()
    ledger_identity = (ledger.pk, ledger.target_id, ledger.content_hash, ledger.loaded_at)
    _, skipped = imported("Second")
    assert skipped.totals[RowResult.IMPORT_TYPE_SKIP] == 1
    ledger.refresh_from_db()
    assert (ledger.pk, ledger.target_id, ledger.content_hash, ledger.loaded_at) == ledger_identity

    # A missing ledger adopts the native unique identity and reports UPDATE.
    ledger.delete()
    group, resource = _group(addon, Workflow, source, ({"_xref": "head", "key": "head", "name": "Adopted"},))
    group.entry.adopt = "key"
    with system_context(reason="native workflow adoption"), transaction.atomic():
        if plan_first:
            AngeeResource.resolve_existing(((group.dataset, resource),))
        adopted = resource.import_data(group.dataset, raise_errors=True, use_transactions=False)
    assert adopted.totals[RowResult.IMPORT_TYPE_UPDATE] == 1
    assert adopted.rows[0].object_id == first.object_id
    assert WorkflowResourceLedger.objects.get(xref="head").target_id == ledger_identity[1]


def test_native_loader_calls_each_dataset_once_and_preserves_skipped_rows(tmp_path: Path, monkeypatch: Any) -> None:
    addon = _addon(tmp_path)
    calls = []
    native = WorkflowDefinitionResource.import_data

    def import_data(self: Any, dataset: Any, **kwargs: Any) -> Any:
        calls.append(self._meta.model)
        return native(self, dataset, **kwargs)

    monkeypatch.setattr(WorkflowDefinitionResource, "import_data", import_data)
    first = _install(addon)
    workflow = Workflow.system_queryset().get(key="resource-install-probe")
    revision = workflow.draft_revision
    assert first.created == 9
    assert calls == [Workflow, Step, Edge]
    calls.clear()
    second = _install(addon)
    assert second == LoadResult(created=0, updated=0, skipped=9)
    assert calls == [Workflow, Step, Edge]
    workflow.refresh_from_db()
    assert workflow.draft_revision == revision
    assert Step.system_queryset().count() == 4
    assert Edge.system_queryset().count() == 4


def test_native_diagnostics_keep_source_row_and_full_transaction_rollback(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    groups = _groups(addon)
    step_group = groups[1][0]
    rows = step_group.dataset.dict
    rows[1]["step_class"] = "unknown-operation"
    headers = step_group.dataset.headers
    step_group.dataset.wipe()
    step_group.dataset.headers = headers
    for row in rows:
        step_group.dataset.append([row[name] for name in headers])
    step_group.source_rows = [2, 7, 8, 12]
    with pytest.raises(ResourceLoadError, match=r"101_workflows.step.yaml: 7:"):
        _load(groups)
    assert not Workflow.system_queryset().exists()
    assert not Step.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()


@pytest.mark.parametrize("facet", [0, 1, 2])
def test_duplicate_new_xref_has_native_row_diagnostic_and_rolls_back(tmp_path: Path, facet: int) -> None:
    groups = _groups(_addon(tmp_path))
    group = groups[facet][0]
    duplicate = dict(group.dataset.dict[0])
    if "key" in duplicate:
        duplicate["key"] = "another-new-row"
    group.dataset.append([duplicate[name] for name in group.dataset.headers])
    group.source_rows.append(42)

    with pytest.raises(ResourceLoadError, match=r"yaml: 42:.*duplicate _xref"):
        _load(groups)

    assert not Workflow.system_queryset().exists()
    assert not Step.system_queryset().exists()
    assert not Edge.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_dry_run_rolls_back_native_rows_ledgers_and_revisions(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    result = _load(_groups(addon), dry_run=True)
    assert result.created == 9
    assert not Workflow.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()
    _install(addon)
    head = Workflow.system_queryset().get(key="resource-install-probe")
    revision = head.draft_revision
    ledgers = list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id"))
    result = _load(_groups(addon, entry_name="Dry rename"), dry_run=True)
    assert result.updated == 1
    head.refresh_from_db()
    assert head.draft_revision == revision
    assert Step.system_queryset().get(key="entry").name == "Entry"
    assert list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id")) == ledgers


def test_omitted_step_config_preserves_operator_value_but_class_change_resets_it(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon, entry_config={"operator": True})
    _install(addon, entry_name="Renamed")
    assert Step.system_queryset().get(key="entry").config == {"operator": True}
    _install(addon, entry_config={})
    assert Step.system_queryset().get(key="entry").config == {}
    _install(addon, entry_config={"operator": True})
    groups = _groups(addon)
    steps = groups[1][0].dataset
    values = steps["step_class"]
    values[0] = "agent_session"
    del steps["step_class"]
    steps.append_col(values, header="step_class")
    _load(groups)
    changed = Step.system_queryset().get(key="entry")
    assert changed.step_class == "agent_session"
    assert changed.config == {}


def test_cross_addon_and_path_omissions_delete_only_exact_source_rows(tmp_path: Path) -> None:
    base = _addon(tmp_path)
    _install(base)
    module = ModuleType("tests.contribution")
    module.__file__ = str(tmp_path / "contribution" / "__init__.py")
    extra = AppConfig(module.__name__, module)
    handle = f"{base.label}.lineage"

    def contribution(owner: AppConfig, source: str, xref: str) -> tuple[ResourceGroup, AngeeResource]:
        return _group(owner, Step, source, ({
            "_xref": xref, "workflow": handle, "key": xref, "name": xref, "step_class": "fixture",
        },))

    path_a, path_b = "resources/install/101_workflows.step.yaml", "resources/install/b.yaml"
    additions = (contribution(extra, path_a, "extra-a"), contribution(extra, path_b, "extra-b"))
    extra_edge = _group(extra, Edge, "resources/install/edges.yaml", ({
        "_xref": "contribution-edge", "workflow": handle,
        "source": f"{base.label}.entry", "target": f"{extra.label}.extra-b",
    },))
    _load((*_groups(base), *additions, extra_edge))
    assert Step.system_queryset().count() == 6
    assert Edge.system_queryset().filter(source__key="entry", target__key="extra-b").exists()
    _load((*_groups(base), _group(extra, Step, path_a, ())))
    assert not Step.system_queryset().filter(key="extra-a").exists()
    assert Step.system_queryset().filter(key="extra-b").exists()
    assert Step.system_queryset().filter(key="entry").exists()
    assert not WorkflowResourceLedger.objects.filter(source_addon=extra.name, xref="extra-a").exists()
    assert WorkflowResourceLedger.objects.filter(source_addon=extra.name, xref="extra-b").exists()


def test_step_omission_rejects_incident_edges_without_deleting_contributions(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    groups = _groups(addon)
    del groups[1][0].dataset[1]  # alpha still has incident edge declarations.
    groups[1][0].source_rows.pop(1)
    before = list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id"))
    with pytest.raises(ResourceLoadError, match="incident edges"):
        _load(groups)
    assert Step.system_queryset().count() == 4
    assert Edge.system_queryset().count() == 4
    assert list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id")) == before


def test_edge_omission_removes_its_ledger_and_then_allows_step_omission(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    groups = _groups(addon)
    edge_group = groups[2][0]
    for index in (2, 0):  # remove both edges incident to alpha
        del edge_group.dataset[index]
        edge_group.source_rows.pop(index)
    _load(groups)
    assert not WorkflowResourceLedger.objects.filter(xref__in=("route", "alpha_final")).exists()
    del groups[1][0].dataset[1]
    groups[1][0].source_rows.pop(1)
    _load(groups)
    assert not Step.system_queryset().filter(key="alpha").exists()
    assert not WorkflowResourceLedger.objects.filter(xref="alpha").exists()
    assert Edge.system_queryset().count() == 2


def test_same_dataset_references_to_earlier_new_heads_use_native_instances(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    group = _group(addon, Workflow, "resources/install/heads.yaml", (
        {"_xref": "error", "key": "error", "name": "Error"},
        {"_xref": "main", "key": "main", "name": "Main", "error_workflow": f"{addon.label}.error"},
    ))
    result = _load((group,))
    assert result.created == 2
    main = Workflow.system_queryset().get(key="main")
    assert main.error_workflow_id == Workflow.system_queryset().get(key="error").pk


def test_unresolved_forward_reference_has_native_row_diagnostic_and_rolls_back(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    group = _group(addon, Workflow, "resources/install/heads.yaml", (
        {"_xref": "main", "name": "Main", "error_workflow": f"{addon.label}.later"},
        {"_xref": "later", "name": "Later"},
    ))
    with pytest.raises(ResourceLoadError, match=r"heads.yaml: 1:.*unresolved xref"):
        _load((group,))
    assert not Workflow.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_workflow_scalar_change_bumps_revision_once_and_retains_children(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.system_queryset().get(key="resource-install-probe")
    revision = head.draft_revision
    groups = _groups(addon)
    groups[0][0].dataset.append_col(["Updated description"], header="description")
    result = _load(groups)
    head.refresh_from_db()
    assert result.updated == 1
    assert head.description == "Updated description"
    assert head.draft_revision == revision + 1
    assert Step.system_queryset().filter(workflow=head).count() == 4
    assert Edge.system_queryset().filter(workflow=head).count() == 4


def test_changed_scalar_step_and_edge_facets_each_advance_the_head_cas_revision(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.system_queryset().get(key="resource-install-probe")
    revision = head.draft_revision
    groups = _groups(addon, entry_name="Updated entry", route_source="beta", route_target="alpha")
    groups[0][0].dataset.append_col(["Updated description"], header="description")

    assert _load(groups) == LoadResult(created=0, updated=3, skipped=6)
    head.refresh_from_db()
    assert head.draft_revision == revision + 3

    assert _load(groups) == LoadResult(created=0, updated=0, skipped=9)
    head.refresh_from_db()
    assert head.draft_revision == revision + 3


def test_unchanged_publication_skips_the_definition_write_lock(tmp_path: Path, monkeypatch: Any) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.system_queryset().get(key="resource-install-probe")
    with system_context(reason="publication lock regression"):
        published = head.publish_if_changed()
    assert published is not None
    _install(addon)

    def unexpected_lock(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unchanged publication must not take a definition write lock.")

    monkeypatch.setattr(type(Workflow.objects), "_definition_write", unexpected_lock)
    with system_context(reason="publication lock regression"):
        assert head.publish_if_changed() is None


def test_legacy_zero_revisions_still_compare_publication_content(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    with system_context(reason="legacy publication regression"):
        head = Workflow.system_queryset().get(key="resource-install-probe")
        first = head.publish_if_changed()
        assert first is not None
        _install(addon, entry_name="Changed legacy entry")
        # Reproduce existing rows receiving the revision field's migration default.
        models.QuerySet(model=Workflow).filter(pk__in=(head.pk, first.pk)).update(draft_revision=0)

        second = head.publish_if_changed()

        assert second is not None
        assert second.steps.get(key="entry").name == "Changed legacy entry"
        assert first.steps.get(key="entry").name == "Entry"


def test_failed_graph_edit_rolls_back_earlier_scalar_and_ledger_updates(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.system_queryset().get(key="resource-install-probe")
    revision = head.draft_revision
    groups = _groups(addon, route_source="entry", route_target="beta")  # duplicates a retained edge
    groups[0][0].dataset.append_col(["Must roll back"], header="description")
    before = list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id"))
    with pytest.raises(ResourceLoadError):
        _load(groups)
    head.refresh_from_db()
    assert head.description == ""
    assert head.draft_revision == revision
    assert list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id")) == before


def test_publication_observes_complete_facets_and_grants_and_failure_rolls_back(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """A final publication failure undoes native rows, ledgers, grants and revisions."""

    addon = _addon(tmp_path)
    groups = _groups(addon)
    groups[0][0].entry.publish = True
    user = _group(addon, get_user_model(), "resources/install/viewer.yaml", (
        {"_xref": "viewer", "username": "workflow-resource-viewer", "password": "!"},
    ))
    groups = (*groups, user)
    grant_entry = ResourceEntry(
        addon=addon, tier=Resource.Tier.INSTALL, source_value="resources/install/grants.yaml", kind="grants",
    )
    grants = GrantGroup(grant_entry, (GrantRow(
        grant_entry, f"{addon.label}.lineage", "viewer", f"{addon.label}.viewer", 1,
    ),))
    relationship_model = active_relationship_model()
    before = relationship_model._default_manager.count()
    native_publish = Workflow.publish_if_changed
    publications = []
    fail = True

    def publish(instance: Workflow, **kwargs: Any) -> Any:
        assert instance.steps.count() == 4
        assert instance.edges.count() == 4
        ref = to_object_ref(instance)
        assert relationship_model._default_manager.filter(
            resource_type=ref.resource_type, resource_id=ref.resource_id, relation="viewer",
        ).exists()
        publication = native_publish(instance, **kwargs)
        publications.append(publication)
        if fail:
            raise ResourceLoadError("publication failed after its writes")
        return publication

    monkeypatch.setattr(Workflow, "publish_if_changed", publish)

    def load(*, dry_run: bool = False) -> Any:
        return WorkflowResourceLedger.objects._import_groups(
            (*[group.entry for group, _ in groups], grant_entry),
            tuple(group for group, _ in groups), (grants,), dry_run=dry_run,
            addon_aliases={addon.name: addon.name, addon.label: addon.name},
        )

    load(dry_run=True)
    assert publications == []
    assert not Workflow.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()
    assert relationship_model._default_manager.count() == before
    with pytest.raises(ResourceLoadError, match="publication failed"):
        load()
    assert not Workflow.system_queryset().exists()
    assert not Step.system_queryset().exists()
    assert not Edge.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()
    assert not get_user_model().system_queryset().filter(username="workflow-resource-viewer").exists()
    assert relationship_model._default_manager.count() == before
    fail = False
    load()
    head = Workflow.system_queryset().get(key="resource-install-probe", published_from__isnull=True)
    assert Workflow.system_queryset().filter(published_from=head).count() == 1
    load()
    assert Workflow.system_queryset().filter(published_from=head).count() == 1
    assert publications[-1] is None


def test_workflow_adoption_replaces_stale_ledger_without_changing_the_wrong_target(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    with system_context(reason="prepare stale workflow ledger"):
        wrong = Workflow.objects.create(key="wrong", name="Unrelated")
        intended = Workflow.objects.create(key="intended", name="Original")
    group, resource = _group(addon, Workflow, "resources/install/heads.yaml", (
        {"_xref": "adopted", "key": "intended", "name": "Updated"},
    ))
    group.entry.adopt = "key"
    ledger = WorkflowResourceLedger.objects.create(
        source_addon=addon.name, source_path=group.entry.source, xref="adopted", tier=Resource.Tier.INSTALL,
        target_model=Workflow._meta.label, target_id=public_id_of(wrong),
        content_hash=resource._row_content_hash(group.dataset.dict[0]),
    )
    with system_context(reason="plan stale workflow ledger replacement"):
        assert WorkflowDefinitionResource._lock_targets(((group, resource),)) == {wrong.pk, intended.pk}
    result = _load(((group, resource),))
    assert result.updated == 1
    ledger.refresh_from_db()
    wrong.refresh_from_db()
    intended.refresh_from_db()
    assert ledger.target_id == public_id_of(intended)
    assert wrong.name == "Unrelated"
    assert intended.name == "Updated"


def test_existing_resource_resolution_retains_omissions_and_uses_native_loader_without_importing(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Batch resolution honors the model's identity policy, including retained rows."""

    addon = _addon(tmp_path)
    groups = _groups(addon)
    _load(groups)
    with system_context(reason="prepare model-owned resource identity"):
        for step in Step.system_queryset():
            WorkflowResourceLedger.objects.filter(
                target_model=Step._meta.label, target_id=public_id_of(step),
            ).update(target_id=f"{step.pk:04}")
    monkeypatch.setattr(Step, "public_id_lookup", classmethod(lambda cls, value: {"pk": value}))
    group, resource = groups[1]
    group.dataset = tablib.Dataset(*group.dataset[:1], headers=group.dataset.headers)
    native = resource.get_instance
    rows = []

    def get_instance(loader: Any, row: Any) -> Any:
        rows.append(row["_xref"])
        return native(loader, row)

    def unexpected_import(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Existing-target resolution must not run import hooks.")

    monkeypatch.setattr(resource, "get_instance", get_instance)
    monkeypatch.setattr(resource, "before_import_row", unexpected_import)
    monkeypatch.setattr(resource, "after_save_instance", unexpected_import)
    monkeypatch.setattr(connection.ops, "bulk_batch_size", lambda fields, objects: 2)
    with system_context(reason="resolve retained workflow facet"):
        with CaptureQueriesContext(connection) as queries:
            resolved, _references = AngeeResource.resolve_existing(((group.dataset, resource),))
        assert resolved[(resource, "entry")].instance == Step.system_queryset().get(key="entry")
        assert resolved[(resource, "alpha")].instance is None
        assert resolved[(resource, "alpha")].retained_instance == Step.system_queryset().get(key="alpha")
    assert sum(f'FROM "{Step._meta.db_table}"' in query["sql"] for query in queries) == 2
    assert rows == ["entry"]
    assert resolved[(resource, "alpha")].ledger == WorkflowResourceLedger.objects.get(xref="alpha")
    assert WorkflowResourceLedger.objects.count() == 9


@pytest.mark.parametrize("new_source_first", [True, False])
def test_resource_source_move_keeps_each_retained_target_before_adoption(
    tmp_path: Path, new_source_first: bool,
) -> None:
    addon = _addon(tmp_path)
    old_group, old_resource = _group(addon, Workflow, "resources/install/old.yaml", ())
    new_group, new_resource = _group(addon, Workflow, "resources/install/new.yaml", (
        {"_xref": "moved", "key": "intended", "name": "Updated"},
    ))
    new_group.entry.adopt = "key"
    with system_context(reason="prepare retained workflow source move"):
        retained = Workflow.objects.create(key="retained", name="Retained")
        intended = Workflow.objects.create(key="intended", name="Intended")
        ledger = WorkflowResourceLedger.objects.create(
            source_addon=addon.name, source_path=old_group.entry.source, xref="moved",
            tier=Resource.Tier.INSTALL, target_model=Workflow._meta.label,
            target_id=public_id_of(retained), content_hash="",
        )
        groups = [(new_group, new_resource), (old_group, old_resource)]
        if not new_source_first:
            groups.reverse()
        resolved, references = AngeeResource.resolve_existing(
            [(group.dataset, resource) for group, resource in groups], references=[(addon.name, "moved")],
        )
        assert resolved[(old_resource, "moved")].instance is None
        assert resolved[(new_resource, "moved")].instance == intended
        assert all(row.ledger == ledger and row.retained_instance == retained for row in resolved.values())
        assert references[(addon.name, "moved")] == intended
        assert WorkflowDefinitionResource._lock_targets(groups) == {retained.pk, intended.pk}


def test_canonical_ledger_hook_runs_once_per_persisted_row_and_never_for_skip(tmp_path: Path, monkeypatch: Any) -> None:
    addon = _addon(tmp_path)
    native = AngeeResource.after_save_instance
    calls = []

    def saved(resource: Any, instance: Any, row: Any, **kwargs: Any) -> None:
        assert instance.pk is not None and not instance._state.adding
        calls.append(row["_xref"])
        native(resource, instance, row, **kwargs)

    monkeypatch.setattr(AngeeResource, "after_save_instance", saved)
    _install(addon)
    assert len(calls) == 9
    assert len(set(calls)) == 9
    calls.clear()
    _install(addon)
    assert calls == []
    _install(addon, entry_name="New name")
    assert calls == ["entry"]


def test_empty_workflow_facet_removes_unreferenced_head_and_ledger(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    source = "resources/install/heads.yaml"
    _load((_group(addon, Workflow, source, ({"_xref": "empty-head", "name": "Empty head"},)),))
    _load((_group(addon, Workflow, source, ()),))
    assert not Workflow.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_invalid_explicit_config_rolls_back_complete_native_install(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    with pytest.raises(ResourceLoadError, match=r"101_workflows.step.yaml: 1:.*config"):
        _install(addon, entry_config=[])
    assert not Workflow.system_queryset().exists()
    assert not Step.system_queryset().exists()
    assert not Edge.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()


@pytest.mark.parametrize("target_model", [Step._meta.label, "missing.Model"])
def test_lock_planning_leaves_ledger_collisions_to_native_source_row_diagnostics(
    tmp_path: Path, target_model: str,
) -> None:
    addon = _addon(tmp_path)
    groups = _groups(addon)
    groups[0][0].source_rows = [9]
    collision = WorkflowResourceLedger.objects.create(
        source_addon=addon.name, source_path="resources/install/other.yaml", xref="lineage",
        tier=Resource.Tier.INSTALL, target_model=target_model, target_id="invalid", content_hash="",
    )
    with pytest.raises(ResourceLoadError, match=r"100_workflows.workflow.yaml: 9:.*xref collision"):
        _load(groups)
    assert not Workflow.system_queryset().exists()
    assert list(WorkflowResourceLedger.objects.values_list("pk", flat=True)) == [collision.pk]


def test_lock_planning_leaves_invalid_adoption_to_native_source_row_diagnostics(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    groups = _groups(addon)
    groups[0][0].entry.adopt = "name"  # Workflow names are not unique adoption identities.
    groups[0][0].source_rows = [6]
    with pytest.raises(ResourceLoadError, match=r"100_workflows.workflow.yaml: 6:.*adopt"):
        _load(groups)
    assert not Workflow.system_queryset().exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_step_composite_adoption_keeps_native_update_and_original_target(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    alpha = Step.system_queryset().get(key="alpha")
    WorkflowResourceLedger.objects.get(xref="alpha").delete()
    groups = _groups(addon)
    groups[1][0].entry.adopt = ("workflow", "key")
    result = _load(groups)
    assert result == LoadResult(created=0, updated=1, skipped=8)
    assert WorkflowResourceLedger.objects.get(xref="alpha").target_id == public_id_of(alpha)
    assert Step.system_queryset().get(key="alpha").pk == alpha.pk


@pytest.mark.parametrize("extra_steps", [0, 10])
def test_lock_planning_batches_ledgers_and_targets_independently_of_row_count(
    tmp_path: Path, django_assert_num_queries: Any, extra_steps: int,
) -> None:
    addon = _addon(tmp_path)
    groups = list(_groups(addon))
    step_group = groups[1][0]
    rows = step_group.dataset.dict
    rows.extend(
        {**rows[1], "_xref": f"extra_{index}", "key": f"extra_{index}"}
        for index in range(extra_steps)
    )
    groups[1] = _group(addon, Step, step_group.entry.source, tuple(rows))
    _load(tuple(groups))
    head = Workflow.system_queryset().get(key="resource-install-probe")

    # Each facet primes its declared and owned ledgers; each target model is read once.
    with system_context(reason="batched workflow lock planning"), django_assert_num_queries(9):
        assert WorkflowDefinitionResource._lock_targets(groups) == {head.pk}


@pytest.mark.parametrize("reference_count", [1, 10])
def test_lock_planning_batches_external_references(
    tmp_path: Path, django_assert_num_queries: Any, reference_count: int,
) -> None:
    addon = _addon(tmp_path)
    ids = set()
    rows = []
    with system_context(reason="prepare external workflow references"):
        for index in range(reference_count):
            head = Workflow.objects.create(name=f"Related {index}")
            ids.add(head.pk)
            WorkflowResourceLedger.objects.create(
                source_addon="tests.related_resources", source_path="heads.yaml", xref=f"head_{index}",
                tier=Resource.Tier.INSTALL, target_model=Workflow._meta.label,
                target_id=public_id_of(head), content_hash="",
            )
            rows.append({
                "_xref": f"head_{index}", "name": f"Declared {index}",
                "error_workflow": f"tests.related_resources.head_{index}",
            })
    group, resource = _group(addon, Workflow, "resources/install/heads.yaml", tuple(rows))
    resource.addon_aliases = {**resource.addon_aliases, "tests.related_resources": "tests.related_resources"}
    # Two facet ledger reads, one related-addon ledger read, one workflow read.
    with system_context(reason="batch external workflow locks"), django_assert_num_queries(4):
        assert WorkflowDefinitionResource._lock_targets(((group, resource),)) == ids
