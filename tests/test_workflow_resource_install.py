"""Focused regressions for model-owned workflow resource installation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import tablib
from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.db import models, router, transaction
from django.db.models.fields import NOT_PROVIDED
from import_export.results import RowResult
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
        dry_run=dry_run, addon_aliases=aliases, using="default",
    )


def _install(addon: AppConfig, **changes: Any) -> LoadResult:
    return _load(_groups(addon, **changes))


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


def test_installer_distinguishes_empty_object_false_and_null(tmp_path: Path) -> None:
    """Native row validation rejects false; explicit nullable JSON remains null."""

    addon = _addon(tmp_path)
    _install(addon, entry_binding={})
    with pytest.raises(ResourceLoadError, match=r"101_workflows.step.yaml: 1:.*input_binding"):
        _install(addon, entry_binding=False)
    assert Step.objects.get(key="entry").input_binding == {}
    _install(addon, entry_binding=None)
    assert Step.objects.get(key="entry").input_binding is None


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


def test_native_results_and_canonical_ledger_cover_create_update_skip_and_adoption(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    source = "resources/install/100_workflows.workflow.yaml"

    def imported(name: str) -> tuple[Any, Any]:
        group, resource = _group(addon, Workflow, source, ({"_xref": "head", "key": "head", "name": name},))
        with system_context(reason="native workflow import results"), transaction.atomic():
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
    workflow = Workflow.objects.get(key="resource-install-probe")
    revision = workflow.draft_revision
    assert first.created == 9
    assert calls == [Workflow, Step, Edge]
    calls.clear()
    second = _install(addon)
    assert second == LoadResult(created=0, updated=0, skipped=9)
    assert calls == [Workflow, Step, Edge]
    workflow.refresh_from_db()
    assert workflow.draft_revision == revision
    assert Step.objects.count() == 4
    assert Edge.objects.count() == 4


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
    assert not Workflow.objects.exists()
    assert not Step.objects.exists()
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

    assert not Workflow.objects.exists()
    assert not Step.objects.exists()
    assert not Edge.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_dry_run_rolls_back_native_rows_ledgers_and_revisions(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    result = _load(_groups(addon), dry_run=True)
    assert result.created == 9
    assert not Workflow.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()
    _install(addon)
    head = Workflow.objects.get(key="resource-install-probe")
    revision = head.draft_revision
    ledgers = list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id"))
    result = _load(_groups(addon, entry_name="Dry rename"), dry_run=True)
    assert result.updated == 1
    head.refresh_from_db()
    assert head.draft_revision == revision
    assert Step.objects.get(key="entry").name == "Entry"
    assert list(WorkflowResourceLedger.objects.values_list("pk", "content_hash", "target_id")) == ledgers


def test_omitted_step_config_preserves_operator_value_but_class_change_resets_it(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon, entry_config={"operator": True})
    _install(addon, entry_name="Renamed")
    assert Step.objects.get(key="entry").config == {"operator": True}
    _install(addon, entry_config={})
    assert Step.objects.get(key="entry").config == {}
    _install(addon, entry_config={"operator": True})
    groups = _groups(addon)
    steps = groups[1][0].dataset
    values = steps["step_class"]
    values[0] = "agent_session"
    del steps["step_class"]
    steps.append_col(values, header="step_class")
    _load(groups)
    changed = Step.objects.get(key="entry")
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
    assert Step.objects.count() == 6
    assert Edge.objects.filter(source__key="entry", target__key="extra-b").exists()
    _load((*_groups(base), _group(extra, Step, path_a, ())))
    assert not Step.objects.filter(key="extra-a").exists()
    assert Step.objects.filter(key="extra-b").exists()
    assert Step.objects.filter(key="entry").exists()
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
    assert Step.objects.count() == 4
    assert Edge.objects.count() == 4
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
    assert not Step.objects.filter(key="alpha").exists()
    assert not WorkflowResourceLedger.objects.filter(xref="alpha").exists()
    assert Edge.objects.count() == 2


def test_same_dataset_references_to_earlier_new_heads_use_native_instances(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    group = _group(addon, Workflow, "resources/install/heads.yaml", (
        {"_xref": "error", "key": "error", "name": "Error"},
        {"_xref": "main", "key": "main", "name": "Main", "error_workflow": f"{addon.label}.error"},
    ))
    result = _load((group,))
    assert result.created == 2
    assert Workflow.objects.get(key="main").error_workflow == Workflow.objects.get(key="error")


def test_unresolved_forward_reference_has_native_row_diagnostic_and_rolls_back(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    group = _group(addon, Workflow, "resources/install/heads.yaml", (
        {"_xref": "main", "name": "Main", "error_workflow": f"{addon.label}.later"},
        {"_xref": "later", "name": "Later"},
    ))
    with pytest.raises(ResourceLoadError, match=r"heads.yaml: 1:.*unresolved xref"):
        _load((group,))
    assert not Workflow.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_workflow_scalar_change_bumps_revision_once_and_retains_children(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.objects.get(key="resource-install-probe")
    revision = head.draft_revision
    groups = _groups(addon)
    groups[0][0].dataset.append_col(["Updated description"], header="description")
    result = _load(groups)
    head.refresh_from_db()
    assert result.updated == 1
    assert head.description == "Updated description"
    assert head.draft_revision == revision + 1
    assert head.steps.count() == 4
    assert head.edges.count() == 4


def test_changed_scalar_step_and_edge_facets_each_advance_the_head_cas_revision(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.objects.get(key="resource-install-probe")
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
    head = Workflow.objects.get(key="resource-install-probe")
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
        head = Workflow.objects.get(key="resource-install-probe")
        first = head.publish_if_changed()
        assert first is not None
        _install(addon, entry_name="Changed legacy entry")
        # Reproduce existing rows receiving the revision field's migration default.
        models.QuerySet(model=Workflow, using="default").filter(pk__in=(head.pk, first.pk)).update(draft_revision=0)

        second = head.publish_if_changed()

        assert second is not None
        assert second.steps.get(key="entry").name == "Changed legacy entry"
        assert first.steps.get(key="entry").name == "Entry"


def test_failed_graph_edit_rolls_back_earlier_scalar_and_ledger_updates(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    head = Workflow.objects.get(key="resource-install-probe")
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
            addon_aliases={addon.name: addon.name, addon.label: addon.name}, using="default",
        )

    load(dry_run=True)
    assert publications == []
    assert not Workflow.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()
    assert relationship_model._default_manager.count() == before
    with pytest.raises(ResourceLoadError, match="publication failed"):
        load()
    assert not Workflow.objects.exists()
    assert not Step.objects.exists()
    assert not Edge.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()
    assert not get_user_model().objects.filter(username="workflow-resource-viewer").exists()
    assert relationship_model._default_manager.count() == before
    fail = False
    load()
    head = Workflow.objects.get(key="resource-install-probe", published_from__isnull=True)
    assert head.published_versions.count() == 1
    load()
    assert head.published_versions.count() == 1
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
    result = _load(((group, resource),))
    assert result.updated == 1
    ledger.refresh_from_db()
    wrong.refresh_from_db()
    intended.refresh_from_db()
    assert ledger.target_id == public_id_of(intended)
    assert wrong.name == "Unrelated"
    assert intended.name == "Updated"


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


def test_resource_load_rejects_split_read_routing_before_any_write(tmp_path: Path, monkeypatch: Any) -> None:
    addon = _addon(tmp_path)
    read = router.db_for_read
    monkeypatch.setattr(
        router, "db_for_read", lambda model, **hints: "replica" if model is Step else read(model, **hints),
    )
    with pytest.raises(ResourceLoadError, match="default authorization database"):
        _install(addon)
    assert not WorkflowResourceLedger.objects.exists()
    assert not Workflow._base_manager.exists()


def test_resource_entry_owners_reject_explicit_and_bound_non_default_aliases(tmp_path: Path, monkeypatch: Any) -> None:
    addon = _addon(tmp_path)
    groups = _groups(addon)
    monkeypatch.setattr(
        WorkflowResourceLedger.objects, "_groups_for",
        lambda *args, **kwargs: (tuple(group.entry for group, _ in groups), tuple(group for group, _ in groups), ()),
    )
    with pytest.raises(ResourceLoadError, match="default authorization database"):
        WorkflowResourceLedger.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL], using="other")
    with pytest.raises(ResourceLoadError, match="default authorization database"):
        WorkflowResourceLedger.objects.db_manager("other").validate_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert not WorkflowResourceLedger.objects.exists()


def test_empty_workflow_facet_removes_unreferenced_head_and_ledger(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    source = "resources/install/heads.yaml"
    _load((_group(addon, Workflow, source, ({"_xref": "empty-head", "name": "Empty head"},)),))
    _load((_group(addon, Workflow, source, ()),))
    assert not Workflow.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_invalid_explicit_config_rolls_back_complete_native_install(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    with pytest.raises(ResourceLoadError, match=r"101_workflows.step.yaml: 1:.*config"):
        _install(addon, entry_config=[])
    assert not Workflow.objects.exists()
    assert not Step.objects.exists()
    assert not Edge.objects.exists()
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
    assert not Workflow.objects.exists()
    assert list(WorkflowResourceLedger.objects.values_list("pk", flat=True)) == [collision.pk]


def test_lock_planning_leaves_invalid_adoption_to_native_source_row_diagnostics(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    groups = _groups(addon)
    groups[0][0].entry.adopt = "name"  # Workflow names are not unique adoption identities.
    groups[0][0].source_rows = [6]
    with pytest.raises(ResourceLoadError, match=r"100_workflows.workflow.yaml: 6:.*adopt"):
        _load(groups)
    assert not Workflow.objects.exists()
    assert not WorkflowResourceLedger.objects.exists()


def test_step_composite_adoption_keeps_native_update_and_original_target(tmp_path: Path) -> None:
    addon = _addon(tmp_path)
    _install(addon)
    alpha = Step.objects.get(key="alpha")
    WorkflowResourceLedger.objects.get(xref="alpha").delete()
    groups = _groups(addon)
    groups[1][0].entry.adopt = ("workflow", "key")
    result = _load(groups)
    assert result == LoadResult(created=0, updated=1, skipped=8)
    assert WorkflowResourceLedger.objects.get(xref="alpha").target_id == public_id_of(alpha)
    assert Step.objects.get(key="alpha").pk == alpha.pk


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
    head = Workflow.objects.get(key="resource-install-probe")

    # Each facet primes its declared and owned ledgers; each target model is read once.
    with system_context(reason="batched workflow lock planning"), django_assert_num_queries(9):
        assert WorkflowDefinitionResource._lock_targets(groups, using="default") == {head.pk}
