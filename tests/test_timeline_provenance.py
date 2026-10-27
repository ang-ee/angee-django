"""Closure and the inverse document-history scope drive provenance events."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import DatabaseError, connection, models
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.identity import public_id_of
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import Channel
from angee.provenance.context import changeset as open_changeset
from angee.provenance.timeline import ChangeKind
from angee.refs import generic_pointer_target
from tests.conftest import create_platform_admin, current_stream, make_integration, vault_for
from tests.test_timeline import timeline_entries

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.provenance.timeline.timeline_kinds"]


def test_closure_admits_the_owner_history(composed_permissions) -> None:
    owner = create_platform_admin("timeline-history-owner")
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    event, = timeline_entries(vault, owner)
    changeset = vault.history.latest("history_id").changeset
    assert event.target_id == public_id_of(changeset)
    assert changeset.state == changeset.State.CLOSED
    assert event.at == changeset.occurred_at
    assert event.target_model == "provenance.Changeset"


def test_only_explicit_closure_invalidates_and_changesets_never_gain_a_transport_feed(
    composed_permissions, monkeypatch,
) -> None:
    owner = create_platform_admin("timeline-explicit-close")
    states, broadcasts = [], []
    original = ChangeKind.records

    def project(self, row):
        states.append(row.state)
        return original(self, row)

    monkeypatch.setattr(ChangeKind, "records", project)
    monkeypatch.setattr(
        "angee.graphql.publishing._broadcast", lambda model, payload: broadcasts.append(model._meta.label),
    )
    with open_changeset(fresh=True):
        vault_for(owner)
        assert states == []
    assert states == ["closed"]
    assert "provenance.Changeset" not in broadcasts


def test_notification_failure_keeps_the_closed_changeset_readable(
    composed_permissions, monkeypatch, caplog,
) -> None:
    owner = create_platform_admin("timeline-history-failure")
    vault = vault_for(owner)
    original = ChangeKind.records

    def fail(self, row):
        list(original(self, row))
        raise DatabaseError("invalidation failed")

    monkeypatch.setattr(ChangeKind, "records", fail)
    with actor_context(owner), open_changeset(fresh=True, change_reason="Still committed"):
        vault.name = "Committed despite invalidation failure"
        vault.save(update_fields=["name"])
    vault.refresh_from_db()
    changeset = vault.history.latest("history_id").changeset
    assert vault.name == "Committed despite invalidation failure"
    assert changeset.state == changeset.State.CLOSED
    assert "Timeline invalidation failed" in caplog.text
    assert timeline_entries(vault, owner)[0].detail == "Still committed"


def test_inverse_history_scope_includes_owned_lines_and_deleted_lines(composed_permissions) -> None:
    owner = create_platform_admin("timeline-document-owner")
    project_model = apps.get_model("projects", "Project")
    milestone_model = apps.get_model("projects", "Milestone")
    with actor_context(owner), open_changeset(fresh=True):
        project = project_model.objects.create(title="Delivery")
        line = milestone_model.objects.create(project=project, name="First")
    for action in ("edit", "delete"):
        with actor_context(owner), open_changeset(fresh=True, change_reason=action):
            if action == "edit":
                line.name = "Second"
                line.save(update_fields=["name"])
            else:
                line.delete()
        event = next(line for line in timeline_entries(project, owner) if line.detail == action)
        boundary = apps.get_model("provenance", "Changeset").objects.with_actor(owner).get(change_reason=action)
        assert event.target_id == public_id_of(boundary)
        changesets = apps.get_model("provenance", "Changeset").objects
        with system_context(reason="test.timeline.history_inverse"):
            assert changesets.touching(project).filter(pk=boundary.pk).exists()
            assert (project_model, project.pk) in set(changesets.filter(pk=boundary.pk).records_touched())


def test_closure_writes_no_timeline_projection(composed_permissions) -> None:
    owner = create_platform_admin("timeline-batch-owner")
    with CaptureQueriesContext(connection) as queries:
        with actor_context(owner), open_changeset(fresh=True):
            for index in range(5):
                vault_for(owner, name=f"Batch {index}")
    inserts = [
        query["sql"] for query in queries.captured_queries
        if query["sql"].startswith("INSERT INTO \"test_timeline_event\"")
    ]
    assert inserts == []


def test_inverse_history_scope_covers_mti_pairs_and_generated_statements(composed_permissions) -> None:
    owner = create_platform_admin("timeline-document-relations")
    party_model = apps.get_model("parties", "Party")
    person_model = apps.get_model("parties", "Person")
    with actor_context(owner), open_changeset(fresh=True):
        person = person_model.objects.create(display_name="Ada", first_met_note="Original")
        other = party_model.objects.create(display_name="Other")
    with actor_context(owner), open_changeset(fresh=True, change_reason="MTI edit"):
        person.first_met_note = "Edited"
        person.save(update_fields=["first_met_note"])
    with actor_context(owner), open_changeset(fresh=True, change_reason="Pair statement"):
        pair = person.same_as.for_pair(person, other).with_actor(owner)
        pair.confirm(aspect="same_as", values={})
    with actor_context(owner), open_changeset(fresh=True, change_reason="Pair statement only"):
        pair.confirm(aspect="same_as", values={}, confidence=0.75)
    with actor_context(owner), open_changeset(fresh=True, change_reason="Statement only"):
        person.with_actor(owner).confirm(aspect="identity", values={"display_name": "Ada"})
    changesets = apps.get_model("provenance", "Changeset").objects
    with system_context(reason="test.timeline.inverse_relations"):
        for detail, records in (
            ("MTI edit", (person,)),
            ("Pair statement", (person, other)),
            ("Pair statement only", (person, other)),
            ("Statement only", (person,)),
        ):
            boundary = changesets.get(change_reason=detail)
            if detail == "Pair statement only":
                assert boundary.touched == [pair.statements.model._meta.label_lower]
            identities = set(changesets.filter(pk=boundary.pk).records_touched())
            for record in records:
                assert (type(record), record.pk) in identities
                assert changesets.touching(record).filter(pk=boundary.pk).exists()
                assert any(line.target_id == public_id_of(boundary) for line in timeline_entries(record, owner))


def test_merge_history_includes_the_unchanged_survivor(composed_permissions) -> None:
    owner = create_platform_admin("timeline-history-merge")
    tag_model = apps.get_model("tags", "Tag")
    with actor_context(owner):
        survivor = tag_model.objects.create(name="Name")
        duplicate = tag_model.objects.create(name="Alias")
        survivor.merge(records=[duplicate.public_id])
    changesets = apps.get_model("provenance", "Changeset").objects
    with system_context(reason="test.timeline.merge_history"):
        boundary = changesets.filter(activity="merge").get()
        assert (tag_model, survivor.pk) in set(changesets.filter(pk=boundary.pk).records_touched())
    assert sum(line.target_id == public_id_of(boundary) for line in timeline_entries(survivor, owner)) == 1


def test_machine_reason_never_reaches_a_user(composed_permissions) -> None:
    """A system-context reason code is silent in the API and the timeline; a sentence passes through."""

    owner = create_platform_admin("timeline-machine-reason")
    with open_changeset(fresh=True, change_reason="integrate.stream.advance"):
        vault = vault_for(owner)
    event, = timeline_entries(vault, owner)
    changeset = vault.history.latest("history_id").changeset
    assert event.target_id == public_id_of(changeset)
    assert changeset.change_reason == "integrate.stream.advance"
    assert changeset.reason_display == ""
    assert event.detail == ""
    with actor_context(owner), open_changeset(fresh=True, change_reason="Renamed after the audit"):
        vault.name = "Audited"
        vault.save()
    assert timeline_entries(vault, owner)[0].detail == "Renamed after the audit"


def test_sync_bookkeeping_writes_no_change_event(composed_permissions) -> None:
    """A changeset that only stamps record links (no readable history) projects nothing: the read would admit none."""

    with system_context(reason="test.timeline.bookkeeping"):
        bridge = make_integration("timeline-bookkeeping", model=Channel)
        stream = current_stream(bridge, "contacts", "book", kind=StreamKind.RECORD_REPLICA)
    changeset_model = apps.get_model("provenance", "Changeset")
    with open_changeset(fresh=True, change_reason="integrate.stream.advance"):
        link = RecordLink.objects.observe(stream, "person:1")
    with system_context(reason="test.timeline.bookkeeping"):
        touched = changeset_model.objects.touching(link).order_by("-pk").first()
    assert touched is not None and touched.state == touched.State.CLOSED
    assert not ChangeKind().eligible(RecordLink)
    assert timeline_entries(link) == []


@pytest.mark.parametrize("origin_id", ["external-key", "999999999999999999999999"])
def test_noninteger_origin_does_not_break_a_readable_history_line(composed_permissions, origin_id):
    owner = create_platform_admin("timeline-foreign-origin")
    with open_changeset(fresh=True, change_reason="External origin"):
        record = vault_for(owner)
    boundary = record.history.latest("history_id").changeset
    models.QuerySet(model=type(boundary)).filter(pk=boundary.pk).update(origin_object_id=origin_id)
    assert timeline_entries(record, owner)[0].detail == "External origin"


def test_self_origin_stays_visible_and_only_other_changes_fold_beneath_it(composed_permissions):
    owner = create_platform_admin("timeline-self-origin")
    with actor_context(owner), open_changeset(fresh=True, change_reason="Parent"):
        record = vault_for(owner)
    parent = record.history.latest("history_id").changeset
    content_type, pk = generic_pointer_target(parent)
    models.QuerySet(model=type(parent)).filter(pk=pk).update(
        origin_content_type=content_type, origin_object_id=str(pk),
    )
    with actor_context(owner), open_changeset(fresh=True, origin=parent, change_reason="Child"):
        record.name = "Changed by parent"
        record.save(update_fields=["name"])
    entry, = timeline_entries(record, owner)
    assert entry.target_id == public_id_of(parent)
    assert [(child.kind, child.count) for child in entry.children] == [("provenance.change", 1)]


def test_history_actor_labels_are_resolved_as_a_batch(composed_permissions):
    owner = create_platform_admin("timeline-batched-actor")
    with actor_context(owner), open_changeset(fresh=True):
        record = vault_for(owner)
    timeline_entries(record, owner)
    with CaptureQueriesContext(connection) as baseline:
        timeline_entries(record, owner)
    for index in range(8):
        with actor_context(owner), open_changeset(fresh=True):
            record.name = f"Edit {index}"
            record.save(update_fields=["name"])
    with CaptureQueriesContext(connection) as grown:
        entries = timeline_entries(record, owner)
    assert len(entries) == 9
    assert all(entry.actor == owner.get_username() for entry in entries)
    assert len(grown) <= len(baseline) + 1
