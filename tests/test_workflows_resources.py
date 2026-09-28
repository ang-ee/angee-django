"""Tests for workflow definitions loaded through addon resources."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.apps import AppConfig
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from rebac import actor_context, system_context

from angee.addons import addon_manifest
from angee.graphql.schema import GraphQLSchemas
from angee.resources.exceptions import ResourceLoadError
from angee.resources.models import Resource
from angee.workflows import models as workflow_models
from angee.workflows.definitions import DefinitionEdit, NodePatch
from angee.workflows.testing.models import Step, Trigger, Workflow
from example.notes.models import Note as AbstractNote
from tests.conftest import SchemaAddon, create_platform_admin, make_addon, write_addon_manifest
from tests.tables import model_tables

_REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkflowResourceLedger(Resource):
    """Concrete resource ledger for workflow resource-load tests."""

    class Meta(Resource.Meta):
        """Django model options for the test ledger."""

        abstract = False
        app_label = "resources"
        db_table = "test_workflows_resource_ledger"


class Note(AbstractNote):
    """Concrete subject for the shipped demo trigger and its native publisher."""

    class Meta(AbstractNote.Meta):
        abstract = False
        app_label = "notes"
        db_table = "test_workflows_resource_note"


@pytest.fixture()
def workflow_resource_tables(transactional_db: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Sync permissions and declare the fixture's readable change-feed subject."""

    del transactional_db
    with (
        override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "example.notes"]),
        model_tables((Note,)),
    ):
        # The source schema resolves Note through the registry after this concrete
        # fixture model is registered, matching the composed-model lifecycle.
        from example.notes.schema import schemas as note_schemas

        schemas = GraphQLSchemas([SchemaAddon(note_schemas)])
        monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
        call_command("rebac", "sync", verbosity=0)
        yield


def test_demo_workflow_resources_publish_lineage_and_leave_trigger_disabled(
    workflow_resource_tables: None,
    tmp_path: Path,
) -> None:
    """The example workflow resource loads, publishes once, and leaves automation disabled."""

    del workflow_resource_tables
    owner = _notes_workflow_addon(tmp_path)

    result = WorkflowResourceLedger.objects.load_addons(
        (owner,),
        tiers=[Resource.Tier.DEMO],
        allow_non_dev=True,
    )

    assert result.created == 7
    with system_context(reason="test workflow resource first load"):
        draft = Workflow.objects.get(name="Note publish approval", status=workflow_models.WorkflowStatus.DRAFT)
        published = list(Workflow.objects.filter(published_from=draft).order_by("version"))
        trigger = Trigger.objects.get(workflow=draft)
        current = Workflow.objects.current_published_for(draft)
        draft_binding = draft.steps.get(key="entry").input_binding
        published_binding = published[0].steps.get(key="entry").input_binding
    assert [row.version for row in published] == [1]
    assert current == published[0]
    assert trigger.enabled is False
    assert trigger.config_mapping["model"] == "notes.note"
    assert trigger.config_mapping["condition"] == {"status": "in_review"}
    trigger.validated_config(require_publisher=True)
    trigger_resource = Path("resources/demo/103_workflows.trigger.yaml")
    assert (Path(owner.path) / trigger_resource).read_bytes() == (
        _REPO_ROOT / "examples/addons/example/notes" / trigger_resource
    ).read_bytes()
    assert draft.error_workflow_id is None
    assert draft_binding == {"kind": "workflow_input", "path": []}
    assert published_binding == {"kind": "workflow_input", "path": []}

    second = WorkflowResourceLedger.objects.load_addons(
        (owner,),
        tiers=[Resource.Tier.DEMO],
        allow_non_dev=True,
    )

    assert second.created == 0
    assert second.updated == 0
    assert second.skipped == 7
    with system_context(reason="test workflow resource reload"):
        assert Workflow.objects.filter(published_from=draft).count() == 1


def test_workflows_parties_resource_backfills_key_across_existing_lineage(
    workflow_resource_tables: None,
    tmp_path: Path,
) -> None:
    """Adding the resource stable key initializes the head and its old versions."""

    del workflow_resource_tables
    source = _REPO_ROOT / "addons/angee/workflows_parties"
    target = tmp_path / "workflows_parties"
    resources = target / "resources" / "install"
    resources.mkdir(parents=True)
    (target / "addon.toml").write_text((source / "addon.toml").read_text())
    for filename in (
        "100_workflows.workflow.yaml",
        "101_workflows.step.yaml",
        "102_workflows.edge.yaml",
    ):
        content = (source / "resources" / "install" / filename).read_text()
        if filename == "100_workflows.workflow.yaml":
            content = content.replace("      key: dedupe_parties\n", "")
        (resources / filename).write_text(content)

    owner = _workflows_parties_addon(target)
    contract = addon_manifest(owner)
    assert contract is not None
    assert contract.resources["install"][0]["adopt"] == "key"
    first = WorkflowResourceLedger.objects.load_addons(
        (owner,),
        tiers=[Resource.Tier.INSTALL],
    )

    assert first.created == 9
    with system_context(reason="test legacy workflow resource load"):
        draft = Workflow.objects.get(name="Deduplicate people", status=workflow_models.WorkflowStatus.DRAFT)
        published = Workflow.objects.get(published_from=draft)
    assert draft.key == ""
    assert draft.description.startswith("Scan for people sharing a normalized handle")
    assert published.key == ""

    current = (source / "resources" / "install" / "100_workflows.workflow.yaml").read_text()
    (resources / "100_workflows.workflow.yaml").write_text(current)
    second = WorkflowResourceLedger.objects.load_addons(
        (owner,),
        tiers=[Resource.Tier.INSTALL],
    )

    assert second.updated == 1
    assert second.skipped == 8
    draft.refresh_from_db()
    published.refresh_from_db()
    assert draft.key == "dedupe_parties"
    assert published.key == draft.key
    with system_context(reason="test workflow resource backfill idempotency"):
        assert Workflow.objects.filter(published_from=draft).count() == 1


def test_resource_republication_preserves_omitted_same_impl_config_until_explicit_clear(
    workflow_resource_tables: None,
    tmp_path: Path,
) -> None:
    """A declaration update preserves operator policy unless config is explicit."""

    del workflow_resource_tables
    owner = _notes_workflow_addon(tmp_path)
    steps_path = Path(owner.path) / "resources" / "demo" / "101_workflows.step.yaml"
    steps_path.write_text(steps_path.read_text().replace(
        "      config: {}\n      input_binding: {kind: workflow_input, path: []}\n",
        "      input_binding: {kind: workflow_input, path: []}\n",
        1,
    ))
    WorkflowResourceLedger.objects.load_addons(
        (owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True,
    )

    with system_context(reason="test operator config before resource republication"):
        draft = Workflow.objects.get(name="Note publish approval", status=workflow_models.WorkflowStatus.DRAFT)
        snapshot = Workflow.objects.definition_snapshot(draft)
        entry = next(node for node in snapshot.nodes if node.key == "entry")
        configured = Workflow.objects.apply_definition(
            draft,
            expected_revision=snapshot.revision,
            edit=DefinitionEdit(node_patches=(NodePatch(
                entry.pk, {"config": {"operator_policy": {"mode": "strict"}}},
            ),)),
        )
        Workflow.objects.publish_definition(draft, expected_revision=configured.revision)

    steps_path.write_text(steps_path.read_text().replace("name: Validate note", "name: Validate source note"))
    WorkflowResourceLedger.objects.load_addons(
        (owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True,
    )
    with system_context(reason="test omitted resource config retention"):
        draft.refresh_from_db()
        assert draft.steps.get(key="entry").config == {"operator_policy": {"mode": "strict"}}
        current = Workflow.objects.current_published_for(draft)
        assert current is not None
        assert current.steps.get(key="entry").config == {"operator_policy": {"mode": "strict"}}
        publication_count = Workflow.objects.filter(published_from=draft).count()

    WorkflowResourceLedger.objects.load_addons(
        (owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True,
    )
    with system_context(reason="test omitted resource config idempotency"):
        assert Workflow.objects.filter(published_from=draft).count() == publication_count

    steps_path.write_text(steps_path.read_text().replace(
        "      step_class: agent_session\n      input_binding: {kind: workflow_input, path: []}\n",
        "      step_class: agent_session\n      config: {}\n      input_binding: {kind: workflow_input, path: []}\n",
        1,
    ))
    WorkflowResourceLedger.objects.load_addons(
        (owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True,
    )
    with system_context(reason="test explicit resource config clear"):
        draft.refresh_from_db()
        assert draft.steps.get(key="entry").config == {}
        current = Workflow.objects.current_published_for(draft)
        assert current is not None
        assert current.steps.get(key="entry").config == {}

        snapshot = Workflow.objects.definition_snapshot(draft)
        entry = draft.steps.get(key="entry")
        configured = Workflow.objects.apply_definition(
            draft,
            expected_revision=snapshot.revision,
            edit=DefinitionEdit(node_patches=(NodePatch(
                entry.pk, {"config": {"operator_policy": {"mode": "replacement"}}},
            ),)),
        )
        Workflow.objects.publish_definition(draft, expected_revision=configured.revision)

    steps_path.write_text(steps_path.read_text().replace(
        "      step_class: agent_session\n      config: {}\n",
        "      step_class: parties_dedupe_scan\n",
        1,
    ))
    edges_path = Path(owner.path) / "resources" / "demo" / "102_workflows.edge.yaml"
    edges_path.write_text(edges_path.read_text().replace(
        "      condition: needs_review\n", "      condition: found\n", 1,
    ))
    WorkflowResourceLedger.objects.load_addons(
        (owner,), tiers=[Resource.Tier.DEMO], allow_non_dev=True,
    )
    with system_context(reason="test changed implementation config reset"):
        draft.refresh_from_db()
        entry = draft.steps.get(key="entry")
        assert entry.step_class == "parties_dedupe_scan"
        assert entry.config == {}


def test_resource_import_cannot_change_enabled_trigger_rule(
    workflow_resource_tables: None, tmp_path: Path,
) -> None:
    """Native resource updates still save through the enabled-rule owner."""

    del workflow_resource_tables
    admin = create_platform_admin("trigger-import-admin")
    with system_context(reason="test enabled trigger resource setup"):
        workflow = Workflow.objects.create(name="Scheduled import", created_by=admin)
        Step.objects.create(
            workflow=workflow, key="start", name="Start", step_class="wait", is_entry=True,
            config={"until": "2099-01-01T00:00:00Z"},
        )
        workflow.publish()
        trigger = Trigger.objects.create(
            workflow=workflow, kind=workflow_models.TriggerKind.SCHEDULE, config={"interval_seconds": 60},
        )
    with actor_context(admin):
        trigger.enable()
    original = dict(trigger.config)
    owner = make_addon(
        name="tests.trigger_rules", path=tmp_path,
        resources={"install": [{"path": "trigger.yaml"}]},
    )
    with system_context(reason="test retained trigger resource identity"):
        WorkflowResourceLedger.objects.create(
            source_addon=owner.name, source_path="trigger.yaml", tier="install", xref="trigger",
            content_hash="previous", target_model="workflows.Trigger", target_id=str(trigger.sqid),
        )
    (tmp_path / "trigger.yaml").write_text(json.dumps({
        "_meta": {"model": "workflows.Trigger"},
        "rows": [{"xref": "trigger", "fields": {
            "config": {"interval_seconds": 120},
        }}],
    }))

    with pytest.raises(ResourceLoadError, match="Disable the trigger before editing its rule"):
        WorkflowResourceLedger.objects.load_addons((owner,), tiers=["install"])

    with system_context(reason="inspect rejected trigger import"):
        trigger.refresh_from_db()
    assert trigger.config == original
    assert trigger.execution_actor_id == admin.pk
    assert trigger.enabled is True


def _notes_workflow_addon(tmp_path: Path) -> AppConfig:
    path = _REPO_ROOT / "examples/addons/example/notes"
    resources = {
        "master": (),
        "install": (),
        "demo": (
            {"path": "resources/demo/100_workflows.workflow.yaml", "publish": True},
            {
                "path": "resources/demo/101_workflows.step.yaml",
                "depends_on": "resources/demo/100_workflows.workflow.yaml",
            },
            {
                "path": "resources/demo/102_workflows.edge.yaml",
                "depends_on": "resources/demo/101_workflows.step.yaml",
            },
            {
                "path": "resources/demo/103_workflows.trigger.yaml",
                "depends_on": "resources/demo/100_workflows.workflow.yaml",
            },
        ),
    }
    target = tmp_path / "notes"
    shutil.copytree(path / "resources", target / "resources")
    # This isolated loader fixture installs the concrete workflows.Workflow subject
    # and the core step registry; composed-host coverage exercises the source addon
    # against notes.Note and its contributed operations.
    workflow_path = target / "resources" / "demo" / "100_workflows.workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text().replace(
            "subject_declaration: notes.note",
            "subject_declaration: workflows.workflow",
        )
    )
    steps_path = target / "resources" / "demo" / "101_workflows.step.yaml"
    steps_path.write_text(
        steps_path.read_text()
        .replace("step_class: note_validate_publication", "step_class: agent_session")
        .replace("step_class: note_publish", "step_class: agent_session")
        .replace(
            "      config: {}\n      is_entry: true",
            "      config: {}\n      input_binding: {kind: workflow_input, path: []}\n      is_entry: true",
            1,
        )
    )
    module = ModuleType("example.notes")
    module.__file__ = str(target / "__init__.py")
    config = AppConfig(module.__name__, module)
    write_addon_manifest(config, resources=resources)
    return config


def _workflows_parties_addon(path: Path) -> AppConfig:
    module = ModuleType("angee.workflows_parties")
    module.__file__ = str(path / "__init__.py")
    return AppConfig(module.__name__, module)
