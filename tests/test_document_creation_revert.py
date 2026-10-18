"""Creation undo removes document rows while retaining their native snapshots."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import actor_context

from angee.changesets import changeset
from tests.conftest import create_user

pytestmark = pytest.mark.usefixtures("composed_tables")

Project = apps.get_model("projects", "Project")
Milestone = apps.get_model("projects", "Milestone")


def test_revert_document_creation_removes_children_before_the_parent_and_keeps_history() -> None:
    owner = create_user("document-creation-owner")
    with actor_context(owner), changeset(fresh=True, change_reason="Create project plan"):
        project = Project.objects.create(title="New project", owner=owner)
        milestone = Milestone.objects.create(project=project, name="First delivery")
    project_id, milestone_id = project.pk, milestone.pk
    creation = project.history.get(history_type="+").changeset
    assert milestone.history.get(history_type="+").changeset_id == creation.pk

    with actor_context(owner):
        reverted = creation.with_actor(owner).revert()

    assert reverted.activity == "revert"
    assert not Milestone._base_manager.filter(pk=milestone_id).exists()
    assert not Project._base_manager.filter(pk=project_id).exists()
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
