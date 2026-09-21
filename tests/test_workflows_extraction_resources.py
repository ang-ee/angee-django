"""Installed extraction child-lineage resource contracts."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest
from django.apps import AppConfig
from rebac import system_context

from angee.resources.models import Resource
from angee.workflows.graph import WorkflowGraph
from angee.workflows.models import WorkflowStatus
from tests.test_workflows_resources import WorkflowResourceLedger
from tests.test_workflows_resources import workflow_resource_tables as _workflow_resource_tables  # noqa: F401
from tests.workflows import Workflow

pytestmark = pytest.mark.usefixtures("_workflow_resource_tables")

_ADDON_ROOT = Path(__file__).resolve().parents[1] / "addons/angee/workflows_extraction"


def _extraction_addon() -> AppConfig:
    module = ModuleType("angee.workflows_extraction")
    module.__file__ = str(_ADDON_ROOT / "__init__.py")
    return AppConfig(module.__name__, module)


def test_install_resources_publish_valid_generic_extraction_child() -> None:
    """The native loader installs and publishes one graph-valid reusable child."""

    result = WorkflowResourceLedger.objects.load_addons(
        (_extraction_addon(),),
        tiers=[Resource.Tier.INSTALL],
    )

    assert result.created == 11
    with system_context(reason="inspect installed extraction child"):
        draft = Workflow.objects.get(key="document_extraction", status=WorkflowStatus.DRAFT)
        published = Workflow.objects.current_published_for(draft)
        assert published is not None
        graph = WorkflowGraph.from_rows(
            published,
            published.steps.all(),
            published.edges.select_related("source", "target"),
        )
        steps = {step.key: step for step in published.steps.all()}

    assert graph.diagnostics() == ()
    assert set(steps) == {
        "prepare_pages",
        "map_pages",
        "recognize_page",
        "collect_carriers",
        "process_evidence",
    }
    preparation_fields = steps["prepare_pages"].input_binding["fields"]
    assert "schema" not in preparation_fields
    assert "engine" not in preparation_fields
    assert preparation_fields["engine_config"] == {
        "kind": "workflow_input",
        "path": ["engine_config"],
    }
    collection_fields = steps["collect_carriers"].input_binding["fields"]
    assert "schema" not in collection_fields
    assert "engine" not in collection_fields
    assert collection_fields["engine_config"] == {
        "kind": "workflow_input",
        "path": ["engine_config"],
    }
    recognition_fields = steps["recognize_page"].input_binding["fields"]
    assert recognition_fields["engine"] == {
        "kind": "workflow_input",
        "path": ["recognition_engine"],
    }
    assert recognition_fields["engine_config"] == {
        "kind": "workflow_input",
        "path": ["engine_config", "recognition_config"],
    }
    assert draft.input_schema["required"] == [
        "files",
        "message_parts",
        "schema",
        "engine",
        "engine_config",
        "recognition_engine",
        "recognition_timeout",
        "model",
        "recognition_model",
        "target_model",
        "target_id",
    ]
    assert [rule["outcome"] for rule in draft.result_rules] == ["processed", "source_hold"]
