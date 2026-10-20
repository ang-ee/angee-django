"""Decision admission and conditional verdicts drive the ledger through publication."""

from __future__ import annotations

from dataclasses import replace

import pytest
from django.db import transaction
from django.test import TestCase
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.decisions.timeline import DecisionKind
from angee.graphql.publishing import publish_change
from angee.identity import public_id_of
from angee.tags.testing.models import Tag
from angee.timeline.testing.models import Event
from tests.conftest import create_platform_admin, create_user, vault_for
from tests.test_timeline import read_timeline

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.decisions.timeline.timeline_kinds"]


@pytest.fixture
def people(composed_tables):
    requester, reviewer, bystander = (
        create_user(f"timeline-{role}") for role in ("requester", "reviewer", "bystander")
    )
    subject = vault_for(requester, name="Timeline notes")
    with system_context(reason="test.timeline.readers"):
        write_relationships([
            RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(user))
            for user in (reviewer, bystander)
        ])
    return requester, reviewer, bystander, subject


def ask(people):
    requester, reviewer, _, subject = people
    return Decision.objects.ask(DecisionRequest(
        kind="note_review", records=(subject,), assignees=(reviewer,), requester=requester,
        proposal=DecisionProposal(alternatives=[
            {"key": "complete", "label": "Complete", "outcome": "completed"},
            {"key": "decline", "label": "Decline", "outcome": "rejected"},
        ]),
    ), actor=requester)


def test_ask_then_decide_updates_the_line_and_live_attention(people) -> None:
    requester, reviewer, _, subject = people
    with transaction.atomic():
        question = ask(people)
        assert not Event.objects.filter(kind="decisions.decision").exists()
    event = Event.objects.for_record(subject).get()
    assert (event.target_model, event.target_pk, event.target_id) == (
        "decisions.Decision", question.pk, public_id_of(question),
    )
    assert event.title == "Note review"
    assert event.detail == f"Waiting on {reviewer.get_username()}"
    assert event.fold_pk is None
    data = read_timeline(subject, reviewer, needsMe=True)
    assert data["timeline"]["entries"][0]["needs_me"]
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 1
    assert not read_timeline(subject, requester, needsMe=True)["timeline"]["entries"]

    Decision.objects.decide(question, actor=reviewer, chosen=["complete"], revision=question.revision)
    event.refresh_from_db()
    assert event.detail == "Complete"
    assert event.actor == str(to_subject_ref(reviewer))
    assert event.actor_label == reviewer.get_username()
    data = read_timeline(subject, reviewer)
    assert not data["timeline"]["entries"][0]["needs_me"]
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0
    assert Event.objects.for_record(subject).count() == 1


def test_record_reader_without_decision_read_sees_no_line_identity_or_count(people) -> None:
    _, reviewer, bystander, subject = people
    ask(people)
    assert len(read_timeline(subject, reviewer)["timeline"]["entries"]) == 1
    data = read_timeline(subject, bystander)
    assert data["timeline"]["entries"] == []
    assert data["timeline"]["kinds"][0]["count"] == 0
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0


def test_subscribers_see_the_committed_projection_before_the_change_broadcast(people, monkeypatch) -> None:
    subject = people[3]
    seen = []

    def broadcast(model, payload):
        if model is Decision:
            seen.append(list(Event.objects.for_record(subject).values_list("target_pk", flat=True)))

    monkeypatch.setattr("angee.graphql.publishing._broadcast", broadcast)
    question = ask(people)
    assert seen == [[question.pk]]


def test_withdraw_publishes_the_owner_update_and_clears_attention(people) -> None:
    requester, reviewer, _, subject = people
    question = ask(people)
    with system_context(reason="test.timeline.withdraw"):
        Decision.objects.withdraw(question, actor=requester)
    assert Event.objects.for_record(subject).get().detail == "Withdrawn"
    assert read_timeline(subject, reviewer)["timeline_attention"] == 0


def test_multiple_writes_queue_one_committed_projection_and_rollback_discards_it(people, monkeypatch) -> None:
    _, reviewer, _, subject = people
    original = DecisionKind.events
    projections = []

    def observe(self, row):
        projections.append(row.pk)
        return original(self, row)

    monkeypatch.setattr(DecisionKind, "events", observe)
    with transaction.atomic():
        with pytest.raises(ValueError, match="rollback"):
            with transaction.atomic():
                ask(people)
                raise ValueError("rollback")
        question = ask(people)
        Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
        assert projections == []
    assert projections == [question.pk]
    assert Event.objects.for_record(subject).get().detail == "Complete"


def test_executed_callbacks_do_not_suppress_a_later_projection_in_the_same_atomic(people) -> None:
    _, reviewer, _, subject = people
    with transaction.atomic():
        with TestCase.captureOnCommitCallbacks(execute=True):
            question = ask(people)
            with pytest.raises(ValueError, match="rollback"):
                with transaction.atomic():
                    raise ValueError("rollback")
            # Rebuild the pending set after the savepoint replaced Django's queue.
            publish_change(question, action="update", update_fields=None)
        assert Event.objects.for_record(subject).get().detail.startswith("Waiting")
        with TestCase.captureOnCommitCallbacks(execute=True):
            Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
        assert Event.objects.for_record(subject).get().detail == "Complete"


def test_stored_labels_and_display_text_share_the_storage_bound(people, monkeypatch) -> None:
    original = DecisionKind.events

    def long_labels(self, row):
        return [replace(event, actor_label="a" * 4096, title="t" * 4096, detail="d" * 4096)
                for event in original(self, row)]

    monkeypatch.setattr(DecisionKind, "events", long_labels)
    question = ask(people)
    event = Event.objects.get(target_pk=question.pk)
    assert len(event.actor_label) == 255
    assert len(event.title) == len(event.detail) == 500


@pytest.mark.parametrize("both_records", [False, True])
def test_merge_keeps_one_occurrence_on_the_survivor_after_reprojection(composed_permissions, both_records) -> None:
    requester = create_platform_admin("timeline-merge-owner")
    reviewer = create_user("timeline-merge-reviewer")
    with actor_context(requester):
        survivor = Tag.objects.create(name="Follow-up")
        duplicate = Tag.objects.create(name="Followup")
    question = Decision.objects.ask(DecisionRequest(
        kind="note_review", records=(survivor, duplicate) if both_records else (duplicate,),
        assignees=(reviewer,), requester=requester,
        proposal=DecisionProposal.model_validate({
            "alternatives": [{"key": "complete", "label": "Complete", "outcome": "completed"}],
        }),
    ), actor=requester)
    assert Event.objects.filter(target_pk=question.pk).count() == (2 if both_records else 1)
    with actor_context(requester):
        survivor.with_actor(requester).merge(records=[public_id_of(duplicate)])
    event = Event.objects.for_record(survivor).get()
    assert not Event.objects.for_record(duplicate).exists()
    Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
    current = Event.objects.for_record(survivor).get()
    assert current.pk == event.pk
    assert current.detail == "Complete"
    assert Event.objects.filter(target_pk=question.pk).count() == 1
