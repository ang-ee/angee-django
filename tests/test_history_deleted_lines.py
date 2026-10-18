"""Deleted document lines retain readable deltas and restorable native snapshots."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.db import models
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import reset_backend
from rebac.schema import parse_zed

from angee.base.changesets import changeset
from angee.base.identity import public_id_of
from angee.base.models import model_history
from angee.provenance.schema import RecordHistory
from angee.testing.permissions import install_permission_schema
from tests.conftest import create_user

pytestmark = pytest.mark.usefixtures("composed_tables")

Project = apps.get_model("projects", "Project")
Milestone = apps.get_model("projects", "Milestone")

LINE_SCHEMA = """
definition auth/user {}
definition projects/project {
    relation owner: auth/user // rebac:field=owner
    relation reader: auth/user
    relation editor: auth/user
    permission create = authenticated
    permission read = owner + reader + editor
    permission write = owner + editor
    permission delete = owner
}
definition projects/milestone {
    relation project: projects/project // rebac:field=project
    permission create = project->write
    permission read = project->read
    permission write = project->write
    permission delete = project->write
    permission read__description = project->write
}
"""


@pytest.fixture
def line_policy(composed_tables: None) -> Iterator[None]:
    del composed_tables
    install_permission_schema(parse_zed(LINE_SCHEMA))
    try:
        yield
    finally:
        reset_backend()


def _plan(owner: Any) -> tuple[Any, Any]:
    with actor_context(owner), changeset(fresh=True, change_reason="Plan delivery"):
        project = Project.objects.create(title="Delivery plan", owner=owner)
        milestone = Milestone.objects.create(project=project, name="Release", description="Private scope")
    return project, milestone


def _remove(owner: Any, milestone: Any) -> Any:
    pk = milestone.pk
    with actor_context(owner), changeset(fresh=True, change_reason="Remove delivery"):
        milestone.delete()
    return Milestone.history.get(id=pk, history_type="-")


def _entry(project: Any, actor: Any, reason: str) -> Any:
    return next(row for row in RecordHistory(project, actor).page(100, 0).entries if row.change_reason == reason)


def _line_deltas(entry: Any, identity: str) -> dict[str, tuple[Any, Any]]:
    return {
        row.field: (row.old, row.new)
        for row in entry.changes
        if row.model == Milestone._meta.label and row.id == identity
    }


def test_deleted_line_shows_prior_values_to_null_and_restores_through_its_parent(line_policy: None) -> None:
    owner = create_user("deleted-line-owner")
    project, milestone = _plan(owner)
    pk, identity = milestone.pk, public_id_of(milestone)
    snapshot = _remove(owner, milestone)

    entry = _entry(project, owner, "Remove delivery")
    deltas = _line_deltas(entry, identity)
    assert deltas["name"] == ("Release", None)
    assert deltas["description"] == ("Private scope", None)
    assert deltas["project"] == (public_id_of(project), None)
    assert entry.can_revert

    with actor_context(owner):
        snapshot.changeset.with_actor(owner).revert()

    restored = Milestone._base_manager.get(pk=pk)
    assert (restored.project_id, restored.name, restored.description) == (project.pk, "Release", "Private scope")


def test_deleted_line_keeps_its_native_field_read_gate(line_policy: None) -> None:
    owner = create_user("deleted-field-owner")
    reader = create_user("deleted-field-reader")
    project, milestone = _plan(owner)
    identity = public_id_of(milestone)
    write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(reader))])
    _remove(owner, milestone)

    entry = _entry(project, reader, "Remove delivery")
    deltas = _line_deltas(entry, identity)
    assert deltas["name"] == ("Release", None)
    assert "description" not in deltas
    assert not entry.can_revert


def test_core_history_owners_scope_changesets_and_admit_one_document(line_policy: None) -> None:
    """HistoryMixin owns document membership; Changeset applies its declared replay gate."""

    owner = create_user("history-owner")
    reader = create_user("history-reader")
    project, milestone = _plan(owner)
    with actor_context(owner), changeset(fresh=True, change_reason="Rename delivery"):
        milestone.name = "Launch"
        milestone.save(update_fields=("name",))
    edit = milestone.history.latest("history_id").changeset
    with actor_context(owner), changeset(fresh=True, change_reason="Other plan"):
        other = Project.objects.create(title="Other", owner=owner)
    unrelated = other.history.latest("history_id").changeset

    assert project.history_tables() == (Project,)
    assert project.history_scope()[Milestone] == models.Q(project_id=project.pk)
    with system_context(reason="test history membership"):
        touching = type(edit).objects.touching(project)
        assert touching.filter(pk=edit.pk).exists()
        assert not touching.filter(pk=unrelated.pk).exists()
    assert project.admits_revert(edit)
    assert not project.admits_revert(unrelated)
    assert edit.revertable(project, actor=owner)
    assert not edit.revertable(project, actor=reader)


def test_core_history_diff_and_redaction_use_native_field_policy(line_policy: None) -> None:
    """ModelHistory owns retained deltas and field-level read redaction."""

    owner = create_user("history-diff-owner")
    reader = create_user("history-diff-reader")
    stranger = create_user("history-diff-stranger")
    project, milestone = _plan(owner)
    write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(reader))])
    with actor_context(owner), changeset(fresh=True, change_reason="Rename milestone"):
        milestone.name = "Launch"
        milestone.save(update_fields=("name",))
    version = milestone.history.latest("history_id")
    history = model_history(Milestone)

    assert history is not None
    delta = history.diff(version, version)
    assert [(change.field, change.old, change.new) for change in delta.changes if change.field == "name"] == [
        ("name", "Release", "Launch"),
    ]
    assert history.redacted(version, owner) == frozenset()
    assert "description" in history.redacted(version, reader)
    assert "name" not in history.redacted(version, reader)
    assert {"name", "description"} <= history.redacted(version, stranger)


def test_existing_unreadable_line_never_borrows_an_old_snapshot_gate(line_policy: None) -> None:
    owner = create_user("moved-line-owner")
    other = create_user("moved-line-other")
    project, milestone = _plan(owner)
    identity = public_id_of(milestone)
    with actor_context(other), changeset(fresh=True):
        private = Project.objects.create(title="Private plan", owner=other)
    with system_context(reason="test ownership transfer"), changeset(fresh=True):
        milestone.project = private
        milestone.save(update_fields=("project",))

    history = RecordHistory(project, owner).page(100, 0)
    assert Milestone._base_manager.filter(pk=milestone.pk).exists()
    assert history.entries
    assert all(not _line_deltas(entry, identity) for entry in history.entries)


def test_create_then_delete_has_no_net_line_delta(line_policy: None) -> None:
    owner = create_user("transient-line-owner")
    with actor_context(owner), changeset(fresh=True):
        project = Project.objects.create(title="Stable plan", owner=owner)
    with actor_context(owner), changeset(fresh=True, change_reason="Transient delivery"):
        milestone = Milestone.objects.create(project=project, name="Temporary")
        identity = public_id_of(milestone)
        milestone.delete()

    assert not _line_deltas(_entry(project, owner, "Transient delivery"), identity)


def test_missing_snapshot_policy_or_relationship_target_denies_read(line_policy: None) -> None:
    owner = create_user("missing-snapshot-owner")
    project, milestone = _plan(owner)
    snapshot = _remove(owner, milestone)
    history = model_history(Milestone)
    assert history is not None
    assert history.snapshot_allows(snapshot, "read", actor=owner)

    install_permission_schema(parse_zed("definition auth/user {}"))
    assert not history.snapshot_allows(snapshot, "read", actor=owner)
    # A target's globally readable policy cannot prove its row still exists.
    install_permission_schema(parse_zed(LINE_SCHEMA.replace(
        "permission read = owner + reader + editor", "permission read = authenticated",
    )))
    with actor_context(owner), changeset(fresh=True):
        project.delete()
    assert not history.snapshot_allows(snapshot, "read", actor=owner)


def test_uncaptured_denial_relation_does_not_become_an_empty_permission_fact(line_policy: None) -> None:
    owner = create_user("unknown-denial-owner")
    project, milestone = _plan(owner)
    identity = public_id_of(milestone)
    _remove(owner, milestone)
    install_permission_schema(parse_zed(LINE_SCHEMA.replace(
        "    permission read__description = project->write",
        "    relation withheld: auth/user\n    permission read__description = project->write - withheld",
    )))

    deltas = _line_deltas(_entry(project, owner, "Remove delivery"), identity)
    assert deltas["name"] == ("Release", None)
    assert "description" not in deltas


def test_existing_record_revert_preserves_a_stored_write_grant(line_policy: None) -> None:
    owner = create_user("stored-revert-owner")
    editor = create_user("stored-revert-editor")
    project, _milestone = _plan(owner)
    write_relationships([RelationshipTuple(to_object_ref(project), "editor", to_subject_ref(editor))])
    with actor_context(editor), changeset(fresh=True):
        editable = Project.objects.get(pk=project.pk)
        editable.title = "Edited plan"
        editable.save(update_fields=("title",))
    edit = project.history.latest("history_id").changeset

    with actor_context(editor):
        edit.with_actor(editor).revert()

    project.refresh_from_db()
    assert project.title == "Delivery plan"


@pytest.mark.parametrize("model_label", ["parties.Party", "parties.Person"])
def test_revert_skips_excluded_counters_and_restores_only_the_owning_mti_fields(model_label: str) -> None:
    owner = create_user(f"excluded-history-{model_label.rsplit('.', 1)[-1].lower()}")
    model = apps.get_model(model_label)
    with actor_context(owner), changeset(fresh=True):
        record = model.objects.create(display_name="Ada", first_met_note="Original context", handle_count=7)
    with actor_context(owner), changeset(fresh=True):
        record.first_met_note = "Edited context"
        record.save(update_fields=("first_met_note",))
    edit = record.history.latest("history_id").changeset

    with actor_context(owner):
        edit.with_actor(owner).revert()

    record.refresh_from_db()
    assert record.first_met_note == "Original context"
    assert record.handle_count == 7


def test_an_excluded_snapshot_column_cannot_grant_through_its_constructor_default() -> None:
    owner = create_user("excluded-snapshot-owner")
    model = apps.get_model("parties", "Party")
    with actor_context(owner), changeset(fresh=True):
        record = model.objects.create(display_name="Ada", handle_count=7)
    snapshot = record.history.latest("history_id")
    history = model_history(model)
    assert history is not None
    try:
        install_permission_schema(parse_zed("""
            definition auth/user {}
            definition parties/party {
                relation empty: parties/party // rebac:const={"target_id":"empty","filters":{"handle_count":0}}
                permission available = authenticated
                permission read = empty->available
            }
        """))
        assert not history.snapshot_allows(snapshot, "read", actor=owner)
    finally:
        reset_backend()
