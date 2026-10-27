"""Creation undo removes document rows while retaining their native snapshots."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import override_settings
from rebac import actor_context

from angee.identity import public_id_of
from angee.provenance.context import changeset
from angee.timeline import schema as timeline_schema
from tests.conftest import addon_schema, create_user, execute_schema
from tests.test_timeline import QUERY, read_timeline

pytestmark = pytest.mark.usefixtures("composed_tables")

Project = apps.get_model("projects", "Project")
Milestone = apps.get_model("projects", "Milestone")


@override_settings(ANGEE_TIMELINE_KINDS=["angee.provenance.timeline.timeline_kinds"])
def test_revert_document_creation_removes_children_before_the_parent_and_keeps_history() -> None:
    owner = create_user("document-creation-owner")
    with actor_context(owner), changeset(fresh=True, change_reason="Create project plan"):
        project = Project.objects.create(title="New project", owner=owner)
        milestone = Milestone.objects.create(project=project, name="First delivery")
    project_id, milestone_id = project.pk, milestone.pk
    creation = project.history.get(history_type="+").changeset
    assert milestone.history.get(history_type="+").changeset_id == creation.pk
    for record in (project, milestone):
        entries = read_timeline(record, owner)["timeline"]["entries"]
        assert any(entry["target_id"] == public_id_of(creation) for entry in entries)

    with actor_context(owner):
        reverted = creation.with_actor(owner).revert()

    assert reverted.activity == "revert"
    assert not Milestone._base_manager.filter(pk=milestone_id).exists()
    assert not Project._base_manager.filter(pk=project_id).exists()
    schema = addon_schema(timeline_schema.schemas, "console")
    for record in (project, milestone):
        result = execute_schema(
            schema, QUERY, {"model": record._meta.label, "id": public_id_of(record)}, user=owner,
        )
        assert result.data is None and result.errors
        assert all(
            isinstance(error.original_error, ValidationError) and error.original_error.code == "not_found"
            for error in result.errors
        )
    assert Project.history.filter(id=project_id, history_type="+", changeset=creation).exists()
    assert Project.history.filter(id=project_id, history_type="-", changeset=reverted).exists()
    versions = Milestone.history.filter(id=milestone_id, project_id=project_id)
    assert versions.filter(history_type="+", changeset=creation).exists()
    assert versions.filter(history_type="-", changeset=reverted).exists()


def test_revert_creation_preserves_a_document_with_a_later_live_child() -> None:
    owner = create_user("document-live-child-owner")
    with actor_context(owner), changeset(fresh=True, change_reason="Create project"):
        project = Project.objects.create(title="Active project", owner=owner)
    creation = project.history.get(history_type="+").changeset
    with actor_context(owner), changeset(fresh=True, change_reason="Plan later delivery"):
        milestone = Milestone.objects.create(project=project, name="Later delivery")

    with actor_context(owner), pytest.raises(ValidationError, match="live references or grants prevent removal"):
        creation.with_actor(owner).revert()

    assert Project._base_manager.filter(pk=project.pk).exists()
    assert Milestone._base_manager.filter(pk=milestone.pk, project_id=project.pk).exists()
    assert not Project.history.filter(id=project.pk, history_type="-").exists()
    assert not Milestone.history.filter(id=milestone.pk, history_type="-").exists()
