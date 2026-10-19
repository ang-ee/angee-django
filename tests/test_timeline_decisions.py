"""A decision projects one timeline line per record it concerns; answering releases its assignees."""

from __future__ import annotations

import pytest
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.decisions.timeline import DecisionKind
from angee.identity import public_id_of
from angee.refs import generic_pointer_target
from angee.timeline.events import sync_events
from angee.timeline.testing.models import Event
from tests.conftest import create_user, vault_for


@pytest.fixture
def people(composed_tables):
    requester, reviewer = (create_user(name) for name in ("timeline-requester", "timeline-reviewer"))
    subject = vault_for(requester, name="Timeline notes")
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    return requester, reviewer, subject


@pytest.mark.django_db
def test_decision_waits_on_its_assignees_until_answered(people) -> None:
    requester, reviewer, subject = people
    request = DecisionRequest(
        kind="note_review", records=(subject,), assignees=(reviewer,), requester=requester,
        proposal=DecisionProposal(alternatives=[
            {"key": "complete", "label": "Complete", "outcome": "completed"},
            {"key": "decline", "label": "Decline", "outcome": "rejected"},
        ]),
    )
    question = Decision.objects.ask(request, actor=requester)
    kind = DecisionKind()
    sync_events(question, kinds=(kind,))
    event_model = Event
    content_type, object_id = generic_pointer_target(subject)
    key = f"decisions.decision:{public_id_of(question)}:{content_type.model_class()._meta.label}/{object_id}"
    event = event_model.objects.get(kind=kind.id)
    assert event.key == key
    assert (event.record_content_type_id, event.record_object_id) == (content_type.pk, object_id)
    assert (event.target_model, event.target_id) == ("decisions.Decision", public_id_of(question))
    assert event.title == "Note review"
    assert event.detail == f"Waiting on {reviewer.get_username()}"
    assert event.actor == "" and event.actor_label == ""
    assert event.parent_id is None
    assert set(event.needs.sudo(reason="test").values_list("pk", flat=True)) == {reviewer.pk}

    answered = Decision.objects.decide(question, actor=reviewer, chosen=["complete"], revision=question.revision)
    sync_events(answered, kinds=(kind,))
    event.refresh_from_db()
    assert event.detail == "Complete"
    assert event.actor == str(to_subject_ref(reviewer))
    assert event.actor_label == reviewer.get_username()
    assert not event.needs.sudo(reason="test").exists()
    assert event_model.objects.filter(kind=kind.id).count() == 1
