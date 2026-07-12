"""Tests for workflow definitions loaded through addon resources."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from rebac import system_context

from angee.addons import AddonContract
from angee.graphql.schema import GraphQLSchemas
from angee.resources.models import Resource
from angee.workflows import models as workflow_models
from tests.workflows import WORKFLOW_DEFINITION_MODELS, Trigger, Workflow, workflow_table_setup


class WorkflowResourceLedger(Resource):
    """Concrete resource ledger for workflow resource-load tests."""

    class Meta(Resource.Meta):
        """Django model options for the test ledger."""

        abstract = False
        app_label = "resources"
        db_table = "test_workflows_resource_ledger"


@dataclass(slots=True)
class Addon:
    """Small addon stand-in exposing the example workflow resources."""

    name: str
    label: str
    path: str
    _addon_contract: AddonContract


@pytest.fixture()
def workflow_resource_tables(transactional_db: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Create concrete workflow definition and resource ledger tables."""

    del transactional_db

    class PublishedLabels:
        """Schema collection stand-in for the example notes change feed."""

        def change_publisher_model_labels(self) -> frozenset[str]:
            return frozenset({"notes.note"})

    models: tuple[type[Any], ...] = (*WORKFLOW_DEFINITION_MODELS, WorkflowResourceLedger)
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: PublishedLabels()))
    with workflow_table_setup(models):
        yield


def test_demo_workflow_resources_publish_lineage_and_leave_trigger_disabled(
    workflow_resource_tables: None,
) -> None:
    """The example workflow resource loads, publishes once, and leaves automation disabled."""

    del workflow_resource_tables
    owner = _notes_workflow_addon()

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
    assert [row.version for row in published] == [1]
    assert current == published[0]
    assert trigger.enabled is False

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


def _notes_workflow_addon() -> Addon:
    path = Path("examples/notes-angee/addons/example/notes").resolve()
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
    return Addon(
        name="example.notes",
        label="notes",
        path=str(path),
        _addon_contract=AddonContract(name="example.notes", resources=resources),
    )
