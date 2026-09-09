"""Saved workflow version comparison and restore contracts."""

from __future__ import annotations

import pytest
from rebac import system_context

from angee.workflows.definitions import DefinitionEdit, NodePatch, StaleDefinitionError
from tests.test_workflow_definition_commands import _draft
from tests.workflows import Step, Workflow

pytest_plugins = ("tests.workflows",)


def test_saved_comparison_separates_semantic_and_presentation_changes(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, tail, _edge = _draft()
    with system_context(reason="version comparison setup"):
        publication = workflow.publish()
        workflow.refresh_from_db()
        Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(
                workflow={"description": "Changed settings"},
                node_patches=(
                    NodePatch(entry.pk, {"name": "Presented differently", "config": {"mode": 1}}),
                    NodePatch(tail.pk, {"key": "renamed"}),
                ),
            ),
        )
        comparison = Workflow.objects.compare_definition(workflow, publication)

    assert comparison.source.pk == publication.pk
    assert comparison.draft_revision == workflow.draft_revision + 1
    assert any(item.kind == "settings" and item.field == "description" for item in comparison.changes)
    assert any(
        item.kind == "step" and item.field == "config" and not item.presentation_only
        for item in comparison.changes
    )
    assert any(item.kind == "step" and item.field == "name" and item.presentation_only for item in comparison.changes)
    assert any(item.kind == "step" and item.change == "removed" and item.key == "tail" for item in comparison.changes)
    assert any(item.kind == "step" and item.change == "added" and item.key == "renamed" for item in comparison.changes)


def test_restore_replaces_only_the_saved_draft_and_uses_revision_cas(workflow_tables: None) -> None:
    del workflow_tables
    workflow, entry, _tail, _edge = _draft()
    with system_context(reason="version restore setup"):
        publication = workflow.publish()
        workflow.refresh_from_db()
        Workflow.objects.apply_definition(
            workflow,
            expected_revision=workflow.draft_revision,
            edit=DefinitionEdit(node_patches=(NodePatch(entry.pk, {"name": "Later draft"}),)),
        )
        workflow.refresh_from_db()
        stale_revision = workflow.draft_revision - 1
        with pytest.raises(StaleDefinitionError):
            Workflow.objects.restore_definition(workflow, publication, expected_revision=stale_revision)
        restored = Workflow.objects.restore_definition(
            workflow, publication, expected_revision=workflow.draft_revision
        )

    assert restored.snapshot.workflow.pk == workflow.pk
    assert restored.snapshot.revision == workflow.draft_revision + 1
    assert [row.name for row in restored.snapshot.nodes if row.key == "entry"] == ["Entry"]
    with system_context(reason="verify immutable publication survived restore"):
        assert Step.objects.get(workflow=publication, key="entry").name == "Entry"
        assert Workflow.objects.get(pk=publication.pk).status == publication.status
