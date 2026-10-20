"""Closure and the inverse document-history scope drive provenance events."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.changesets import changeset as open_changeset
from angee.provenance.timeline import ChangeKind
from angee.timeline.testing.models import Event
from tests.conftest import create_platform_admin, vault_for

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.provenance.timeline.timeline_kinds"]


def test_closure_projects_without_reading_history_inside_the_owner_transaction(composed_permissions) -> None:
    owner = create_platform_admin("timeline-history-owner")
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    event = Event.objects.for_record(vault).get(kind="provenance.change")
    changeset = apps.get_model("provenance", "Changeset")._base_manager.get(pk=event.target_pk)
    assert changeset.state == changeset.State.CLOSED
    assert event.at == changeset.occurred_at
    assert event.target_model == "provenance.Changeset"
    assert event.fold_pk is None


def test_only_explicit_closure_projects_and_changesets_never_gain_a_transport_feed(
    composed_permissions, monkeypatch,
) -> None:
    owner = create_platform_admin("timeline-explicit-close")
    states, broadcasts = [], []
    original = ChangeKind.events

    def project(self, row):
        states.append(row.state)
        return original(self, row)

    monkeypatch.setattr(ChangeKind, "events", project)
    monkeypatch.setattr(
        "angee.graphql.publishing._broadcast", lambda model, payload: broadcasts.append(model._meta.label),
    )
    with open_changeset(fresh=True):
        vault_for(owner)
        assert states == []
    assert states == ["closed"]
    assert "provenance.Changeset" not in broadcasts


def test_projection_failure_rolls_back_its_writes_but_keeps_the_closed_changeset(
    composed_permissions, monkeypatch, caplog,
) -> None:
    owner = create_platform_admin("timeline-history-failure")
    vault = vault_for(owner)
    before = Event.objects.count()
    original = ChangeKind.events

    def fail(self, row):
        list(original(self, row))
        Event.objects.all().delete()
        raise DatabaseError("projection failed")

    monkeypatch.setattr(ChangeKind, "events", fail)
    with actor_context(owner), open_changeset(fresh=True, change_reason="Still committed"):
        vault.name = "Committed despite projection failure"
        vault.save(update_fields=["name"])
    vault.refresh_from_db()
    changeset = vault.history.latest("history_id").changeset
    assert vault.name == "Committed despite projection failure"
    assert changeset.state == changeset.State.CLOSED
    assert Event.objects.count() == before
    assert "Timeline projection failed" in caplog.text


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
        event = Event.objects.for_record(project).get(detail=action)
        changesets = apps.get_model("provenance", "Changeset").objects
        with system_context(reason="test.timeline.history_inverse"):
            assert changesets.touching(project).filter(pk=event.target_pk).exists()
            assert (project_model, project.pk) in set(changesets.filter(pk=event.target_pk).records_touched())


def test_closure_batches_all_lines_for_one_target_in_one_upsert(composed_permissions) -> None:
    owner = create_platform_admin("timeline-batch-owner")
    with CaptureQueriesContext(connection) as queries:
        with actor_context(owner), open_changeset(fresh=True):
            for index in range(5):
                vault_for(owner, name=f"Batch {index}")
    inserts = [
        query["sql"] for query in queries.captured_queries
        if query["sql"].startswith("INSERT INTO \"test_timeline_event\"")
    ]
    assert len(inserts) == 1 and "ON CONFLICT" in inserts[0]


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
                assert Event.objects.for_record(record).filter(target_pk=boundary.pk).exists()


def test_merge_history_projects_the_unchanged_survivor(composed_permissions) -> None:
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
    assert Event.objects.for_record(survivor).filter(target_pk=boundary.pk).count() == 1


def test_machine_reason_never_reaches_a_user(composed_permissions) -> None:
    """A system-context reason code is silent in the API and the timeline; a sentence passes through."""

    owner = create_platform_admin("timeline-machine-reason")
    with open_changeset(fresh=True, change_reason="integrate.stream.advance"):
        vault = vault_for(owner)
    event = Event.objects.for_record(vault).get(kind="provenance.change")
    changeset = apps.get_model("provenance", "Changeset")._base_manager.get(pk=event.target_pk)
    assert changeset.change_reason == "integrate.stream.advance"
    assert changeset.reason_display == ""
    assert event.detail == ""
    with actor_context(owner), open_changeset(fresh=True, change_reason="Renamed after the audit"):
        vault.name = "Audited"
        vault.save()
    latest = Event.objects.for_record(vault).filter(kind="provenance.change").order_by("-at", "-pk").first()
    assert latest is not None and latest.detail == "Renamed after the audit"
