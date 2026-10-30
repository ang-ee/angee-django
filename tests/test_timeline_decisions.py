"""Decision admission and verdicts are read directly through owner policy."""

from __future__ import annotations

import pytest
from django.db import models, transaction
from django.utils import timezone
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.models.identity import public_id_of
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.projects.testing.models import Task
from angee.tags.testing.models import Tag
from tests.conftest import create_platform_admin, create_user, vault_for
from tests.test_timeline import read_timeline, timeline_entries

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
        assert len(timeline_entries(subject, reviewer)) == 1
    event, = timeline_entries(subject, reviewer)
    assert (event.target_model, event.target_id) == ("decisions.Decision", public_id_of(question))
    assert event.title == "Note review"
    assert event.detail == f"Waiting on {reviewer.get_username()}"
    data = read_timeline(subject, reviewer, needsMe=True)
    assert data["timeline"]["entries"][0]["needs_me"]
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 1
    assert not read_timeline(subject, requester, needsMe=True)["timeline"]["entries"]

    Decision.objects.decide(question, actor=reviewer, chosen=["complete"], revision=question.revision)
    original_key = event.key
    event, = timeline_entries(subject, reviewer)
    assert event.key == original_key
    assert event.detail == "Complete"
    assert event.actor == reviewer.get_username()
    data = read_timeline(subject, reviewer)
    assert not data["timeline"]["entries"][0]["needs_me"]
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0
    assert len(timeline_entries(subject, reviewer)) == 1


def test_record_reader_without_decision_read_sees_no_line_identity_or_count(people) -> None:
    _, reviewer, bystander, subject = people
    ask(people)
    assert len(read_timeline(subject, reviewer)["timeline"]["entries"]) == 1
    data = read_timeline(subject, bystander)
    assert data["timeline"]["entries"] == []
    assert data["timeline"]["kinds"][0]["count"] == 0
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0


def test_unassigned_attention_uses_answer_permission_without_claiming_others_assignments(people) -> None:
    requester, reviewer, bystander, subject = people
    admin = create_platform_admin("timeline-unassigned-admin")
    decision = Decision.objects.ask(DecisionRequest(
        kind="unassigned_review", records=(subject,), requester=requester,
        proposal=DecisionProposal(alternatives=[{"key": "done", "label": "Done", "outcome": "done"}]),
    ), actor=requester)
    assert read_timeline(subject, admin)["timeline_attention"] == 1
    assert read_timeline(subject, requester)["timeline_attention"] == 0
    assert read_timeline(subject, bystander)["timeline"]["entries"] == []
    Decision.objects.decide(decision, actor=admin, revision=decision.revision, chosen=["done"])
    ask(people)
    assert read_timeline(subject, admin)["timeline_attention"] == 0
    assert read_timeline(subject, reviewer)["timeline_attention"] == 1


def test_subscribers_read_committed_sources_at_the_change_broadcast(people, monkeypatch) -> None:
    subject = people[3]
    seen = []

    def broadcast(model, payload):
        if model is Decision:
            assert not transaction.get_connection().in_atomic_block
            seen.append([line.target_id for line in timeline_entries(subject, people[1])])

    monkeypatch.setattr("angee.graphql.publishing._broadcast", broadcast)
    question = ask(people)
    assert seen == [[public_id_of(question)]]


def test_withdraw_publishes_the_owner_update_and_clears_attention(people) -> None:
    requester, reviewer, _, subject = people
    question = ask(people)
    with system_context(reason="test.timeline.withdraw"):
        Decision.objects.withdraw(question, actor=requester)
    assert timeline_entries(subject, reviewer)[0].detail == "Withdrawn"
    assert read_timeline(subject, reviewer)["timeline_attention"] == 0


@pytest.mark.parametrize("both_records", [False, True])
def test_merge_keeps_one_occurrence_on_the_survivor(composed_permissions, both_records) -> None:
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
    assert len(timeline_entries(duplicate, reviewer)) == 1
    assert len(timeline_entries(survivor, reviewer)) == int(both_records)
    with actor_context(requester):
        survivor.with_actor(requester).merge(records=[public_id_of(duplicate)])
    event, = timeline_entries(survivor, reviewer)
    Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
    current, = timeline_entries(survivor, reviewer)
    assert current.key == event.key
    assert current.detail == "Complete"
    assert len(timeline_entries(survivor, reviewer)) == 1


def test_rollback_discards_invalidations_and_source_rows(people, monkeypatch):
    _, reviewer, _, subject = people
    broadcasts = []
    monkeypatch.setattr("angee.graphql.publishing._broadcast", lambda model, payload: broadcasts.append(payload))
    with transaction.atomic():
        with pytest.raises(ValueError, match="rollback"):
            with transaction.atomic():
                ask(people)
                raise ValueError("rollback")
        assert timeline_entries(subject, reviewer) == []
        assert broadcasts == []
        question = ask(people)
        Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
        assert broadcasts == []
    assert broadcasts
    assert timeline_entries(subject, reviewer)[0].detail == "Complete"


def test_union_uses_one_global_order_and_cursor_across_kinds(people, settings, composed_permissions, activity_catalog):
    requester, reviewer, bystander, subject = people
    settings.ANGEE_TIMELINE_KINDS = [
        "angee.decisions.timeline.timeline_kinds", "angee.projects.timeline.timeline_kinds",
    ]
    questions = [ask(people) for _ in range(3)]
    hidden = ask((requester, bystander, reviewer, subject))
    with actor_context(requester):
        tasks = [Task.objects.schedule(subject, title=f"Task {index}", assignee=reviewer if index < 2 else None)
                 for index in range(3)]
    at = timezone.now()
    # Freeze only fixture timestamps, including the invisible row. The page must
    # scope before LIMIT and totally order equal times across source tables.
    for model, rows in ((Decision, [*questions, hidden]), (Task, tasks)):
        models.QuerySet(model=model).filter(pk__in=[row.pk for row in rows]).update(created_at=at)
    expected = [public_id_of(row) for row in [*reversed(questions), *reversed(tasks)]]
    pages, identities, cursor = [], [], None
    while True:
        page = read_timeline(subject, reviewer, limit=2, before=cursor)["timeline"]
        pages.append(page)
        identities.extend(line["target_id"] for line in page["entries"])
        if not page["has_more"]:
            assert page["next_before"] is None
            break
        cursor = page["next_before"]
    assert identities == expected
    assert all(page["kinds"] == pages[0]["kinds"] and page["needs_me"] == 5 for page in pages)
    assert {kind["id"]: kind["count"] for kind in pages[0]["kinds"]} == {
        "decisions.decision": 3, "projects.task": 3,
    }
    selected = read_timeline(subject, reviewer, kinds=["projects.task"], needsMe=True)["timeline"]
    assert [line["target_id"] for line in selected["entries"]] == [public_id_of(row) for row in reversed(tasks[:2])]
    assert selected["kinds"] == pages[0]["kinds"] and selected["needs_me"] == 5
