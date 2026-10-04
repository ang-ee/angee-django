"""A question records a permissioned, optimistic verdict without applying actions."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from pydantic import ValidationError as ContractError
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.mixins import StaleRevisionError
from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.signals import decision_answered
from angee.decisions.testing.models import Decision
from tests.conftest import create_user, vault_for


@pytest.fixture
def people(composed_tables):
    requester, reviewer, other, outsider = (
        create_user(name) for name in ("requester", "reviewer", "other", "outsider")
    )
    subject = vault_for(requester, name="Document notes")
    write_relationships(
        [RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(person)) for person in (reviewer, other)]
    )
    return requester, reviewer, other, outsider, subject


def request_for(people, **changes):
    requester, reviewer, _other, _outsider, subject = people
    return DecisionRequest(
        **{
            "kind": "note_review",
            "records": (subject,),
            "assignees": (reviewer,),
            "proposal": DecisionProposal(
                alternatives=[
                    {"key": "complete", "label": "Complete", "outcome": "completed"},
                    {"key": "decline", "label": "Decline", "outcome": "rejected"},
                ]
            ),
            **changes,
        }
    )


def test_one_question_lifecycle_and_audit(people):
    requester, reviewer, _other, _outsider, subject = people
    question = Decision.objects.ask(request_for(people), actor=requester)
    assert question.requester_id == requester.pk and question.is_open and question.verdict is None
    assert question.records.with_actor(reviewer).get().object_id == subject.pk
    answered = Decision.objects.decide(question, actor=reviewer, chosen=["complete"], revision=question.revision)
    assert answered.verdict == ["complete"] and not answered.is_open
    assert answered.answered_by_id == reviewer.pk and answered.answered_at
    assert answered.revision == question.revision + 1
    assert system_queryset(Decision).count() == 1


@pytest.mark.parametrize("chosen", [[], ["unknown"], ["complete", "complete"], ["complete", "decline"], "complete"])
def test_invalid_choices_never_close_or_increment(people, chosen):
    requester, reviewer, *_ = people
    question = Decision.objects.ask(request_for(people), actor=requester)
    with pytest.raises(ValidationError):
        Decision.objects.decide(question, actor=reviewer, chosen=chosen, revision=question.revision)
    question.refresh_from_db()
    assert question.verdict is None and question.revision == 1


def test_multiple_verdict_has_authored_order_and_one_audit(people):
    requester, reviewer, *_ = people
    proposal = request_for(people).proposal.model_copy(update={"multiple": True})
    question = Decision.objects.ask(request_for(people, proposal=proposal), actor=requester)
    answered = Decision.objects.decide(question, actor=reviewer, chosen=["decline", "complete"])
    assert answered.verdict == ["complete", "decline"]
    with pytest.raises(ValidationError):
        Decision.objects.decide(question, actor=reviewer, chosen=["decline"])
    with pytest.raises(StaleRevisionError):
        Decision.objects.decide(question, actor=reviewer, chosen=["decline"], revision=question.revision)


def test_readers_requesters_and_removed_assignees_cannot_answer(people):
    requester, reviewer, other, outsider, subject = people
    question = Decision.objects.ask(request_for(people), actor=requester)
    assert question.with_actor(requester).has_access("read")
    for person in (requester, other, outsider):
        with pytest.raises(PermissionDenied):
            Decision.objects.decide(question, actor=person, chosen=["complete"])
    with system_context(reason="test.remove_assignee"):
        system_queryset(Decision).get(pk=question.pk).assignees.remove(reviewer)
    with pytest.raises(PermissionDenied):
        Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
    subject.with_actor(requester).name = "A direct edit"
    subject.save(update_fields=["name"])
    question.refresh_from_db()
    assert question.is_open


def test_two_assignees_still_answer_one_question(people):
    requester, reviewer, other, *_ = people
    question = Decision.objects.ask(request_for(people, assignees=(reviewer, other)), actor=requester)
    Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
    with pytest.raises(StaleRevisionError):
        Decision.objects.decide(question, actor=other, chosen=["decline"], revision=question.revision)
    assert system_queryset(Decision).get(pk=question.pk).answered_by_id == reviewer.pk


def test_inactive_assignee_and_self_requester_are_not_admitted(people):
    requester, reviewer, *_ = people
    with pytest.raises(ValidationError):
        Decision.objects.ask(request_for(people, assignees=(requester,)), actor=requester)
    with system_context(reason="test.inactive_assignee"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        Decision.objects.ask(request_for(people), actor=requester)
    assert not system_queryset(Decision).exists()


def test_unreadable_concern_rolls_back_admission(people):
    requester, reviewer, other, outsider, _subject = people
    private = vault_for(outsider)
    with pytest.raises(PermissionDenied):
        Decision.objects.ask(request_for(people, records=(private,)), actor=requester)
    assert not system_queryset(Decision).exists()


def test_empty_and_duplicate_alternatives_are_rejected():
    for alternatives in ([], [{"key": "yes", "label": "Yes", "outcome": "done"}] * 2):
        with pytest.raises(ContractError):
            DecisionProposal(alternatives=alternatives)


def test_answer_signal_only_fires_after_commit(people):
    requester, reviewer, *_ = people
    question = Decision.objects.ask(request_for(people), actor=requester)
    received = []

    def collect(sender, decision, **kwargs):
        received.append(decision.pk)

    decision_answered.connect(collect, weak=False)
    try:
        with pytest.raises(RuntimeError), transaction.atomic():
            Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
            assert received == []
            raise RuntimeError("Roll back")
        assert received == []
        Decision.objects.decide(question, actor=reviewer, chosen=["complete"])
        assert received == [question.pk]
    finally:
        decision_answered.disconnect(collect)


def test_verdict_audit_is_database_consistent(people):
    requester, *_ = people
    question = Decision.objects.ask(request_for(people), actor=requester)
    with pytest.raises(IntegrityError), transaction.atomic():
        system_queryset(Decision).filter(pk=question.pk).owner_update(verdict=["complete"])
