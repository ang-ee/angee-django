"""The asking owner replaces an open question atomically; escalation is a replacement with new assignees."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.models.locks import StaleRevisionError
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.signals import decision_answered
from angee.decisions.testing.models import Decision
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import create_user, vault_for


@pytest.fixture
def asked(db):
    requester, reviewer, lead = (create_user(name) for name in ("sp-requester", "sp-reviewer", "sp-lead"))
    subject = vault_for(requester, name="Venue hold")
    write_relationships(
        [RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(person)) for person in (reviewer, lead)]
    )
    question = Decision.objects.ask(request(subject, requester, reviewer), actor=requester)
    return requester, reviewer, lead, subject, question


def request(subject, requester, *assignees):
    return DecisionRequest(
        kind="hold_review",
        records=(subject,),
        assignees=assignees,
        requester=requester,
        proposal=DecisionProposal(
            alternatives=[
                {"key": "keep", "label": "Keep the hold", "outcome": "kept"},
                {"key": "release", "label": "Release it", "outcome": "released"},
            ]
        ),
    )


def supersede(question, replacement_request, *, actor, reason="Escalated"):
    with system_context(reason="tests.decisions.supersede"):
        return Decision.objects.supersede(question, replacement_request, actor=actor, reason=reason)


def test_escalation_replaces_the_question_and_links_it_in_one_step(asked):
    requester, reviewer, lead, subject, question = asked
    announced = []

    def collect(sender, decision, **kwargs):
        announced.append((decision.pk, decision.verdict, decision.superseded_by_id))

    decision_answered.connect(collect, weak=False)
    try:
        replacement = supersede(question, request(subject, requester, lead), actor=requester)
    finally:
        decision_answered.disconnect(collect)
    replaced = Decision.system_queryset().get(pk=question.pk)
    assert replaced.verdict == [] and replaced.superseded_by_id == replacement.pk
    assert replaced.answered_by_id == requester.pk and replaced.withdrawal_reason == "Escalated"
    assert replaced.verdict_label == "Superseded"
    assert announced == [(question.pk, [], replacement.pk)]
    assert list(Decision.system_queryset().open().values_list("pk", flat=True)) == [replacement.pk]
    with system_context(reason="tests.decisions.assignees"):
        assert list(replacement.assignees.values_list("pk", flat=True)) == [lead.pk]
        assert list(replaced.assignees.values_list("pk", flat=True)) == [reviewer.pk]


def test_a_stale_answer_to_the_replaced_question_is_refused(asked):
    requester, reviewer, lead, subject, question = asked
    replacement = supersede(question, request(subject, requester, lead), actor=requester)
    with pytest.raises(StaleRevisionError):
        question.decide(actor=reviewer, chosen=["keep"], revision=question.revision)
    with pytest.raises(ValidationError, match="reload"):
        question.decide(actor=reviewer, chosen=["keep"])
    assert replacement.decide(actor=lead, chosen=["keep"]).verdict == ["keep"]


def test_only_the_asking_owner_supersedes(asked):
    requester, _reviewer, lead, subject, question = asked
    with pytest.raises(PermissionDenied, match="system context"):
        Decision.objects.supersede(question, request(subject, requester, lead), actor=requester, reason="Escalated")
    assert Decision.system_queryset().count() == 1 and Decision.system_queryset().get().is_open


def test_a_replacement_needs_a_reason(asked):
    requester, _reviewer, lead, subject, question = asked
    with pytest.raises(ValidationError, match="reason"):
        supersede(question, request(subject, requester, lead), actor=requester, reason=" ")
    assert Decision.system_queryset().count() == 1


def test_an_answered_question_is_not_replaced(asked):
    requester, reviewer, lead, subject, question = asked
    question.decide(actor=reviewer, chosen=["release"])
    with pytest.raises(ValidationError, match="reload"):
        supersede(question, request(subject, requester, lead), actor=requester)
    assert Decision.system_queryset().count() == 1


def test_a_refused_replacement_leaves_the_question_open(asked):
    requester, _reviewer, _lead, _subject, question = asked
    private = vault_for(create_user("sp-stranger"), name="Unrelated hold")
    with pytest.raises(PermissionDenied):
        supersede(question, request(private, requester), actor=requester)
    assert Decision.system_queryset().count() == 1
    question.refresh_from_db()
    assert question.is_open and question.superseded_by_id is None and question.revision == 1


def test_a_question_a_workflow_step_holds_is_replaced_only_by_that_step(execution, register_step):
    actor, _sent = execution
    subject = vault_for(actor, name="Workflow hold")

    class HoldReview(DecisionStep[None, None, None]):
        key = "hold_review"
        outcomes = {"kept": "Kept", "released": "Released"}

        def ask(self, ctx):
            return ctx.ask(request(subject, None))

    register_step(HoldReview)
    workflow = load_workflow(
        {
            "nodes": {"review": {"step": HoldReview.key}},
            "results": [{"from": "review", "when": [outcome], "as": outcome} for outcome in ("kept", "released")],
        },
        actor=actor,
    )
    run = start_run(workflow, actor=actor, subject=subject)
    run_until(run)
    held = Decision.system_queryset().get(pk=StepRun.system_queryset().get(run=run).decision_id)
    with pytest.raises(ValidationError, match="requesting owner"):
        supersede(held, request(subject, None), actor=actor)
    assert Decision.system_queryset().count() == 1
    assert Decision.system_queryset().get(pk=held.pk).is_open
