"""Independent questions resume their asking step through normal owner writes."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.actors import is_sudo

from angee.base.identity import public_id_of
from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.workflows.reviews import DecisionStep, apply_proposals
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import decide, load_workflow, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun
from tests.conftest import vault_for


class ReviewPrefix(Step[None, None, None]):
    key = "review_prefix"

    def run(self, ctx):
        return ctx.done(ctx.input)


@pytest.fixture
def review(execution, register_step):
    actor, sent = execution
    with system_context(reason="review participants"):
        people = tuple(get_user_model().objects.create_user(username=f"reviewer-{index}") for index in range(2))
    reference = vault_for(actor)
    write_relationships(
        [RelationshipTuple(to_object_ref(reference), "viewer", to_subject_ref(person)) for person in people]
    )

    class Question(DecisionStep[None, None, None]):
        key = "question"
        outcomes = {"approved": "Approved", "rejected": "Rejected", "done": "Chosen outcomes"}
        review_subject = reference

        def ask(self, ctx):
            participants = people if ctx.input.get("two") else people[:1]
            return ctx.ask(
                *(
                    DecisionRequest(
                        kind="question",
                        records=(self.review_subject,),
                        assignees=(person,),
                        proposal=DecisionProposal(
                            multiple=ctx.input.get("multiple", False),
                            alternatives=[
                                {
                                    "key": "approve",
                                    "label": "Approve",
                                    "outcome": "approved",
                                    "actions": {
                                        public_id_of(self.review_subject): {"fields": {"name": {"set": "Applied"}}}
                                    },
                                },
                                {"key": "reject", "label": "Keep current record", "outcome": "rejected"},
                            ],
                        ),
                    )
                    for person in participants
                ),
                state={"value": 17},
            )

        def continue_with(self, ctx, decisions, outcomes):
            assert not is_sudo() and ctx.actor.pk == ctx.run.run_as_id
            assert ctx.state == {"value": 17}
            if ctx.input.get("invalid"):
                Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Rolled back")
                raise ValidationError("Application unavailable.")
            return super().continue_with(ctx, decisions, outcomes)

    register_step(Question)
    return actor, people, sent, Question


def start_review(review, *, input=None, graph=None):
    actor, _people, _sent, question = review
    workflow = load_workflow(
        graph
        or {
            "nodes": {"review": {"step": "question"}},
            "results": [
                {"from": "review", "when": [outcome], "as": outcome} for outcome in ("approved", "rejected", "done")
            ],
        },
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=question.review_subject, input=input or {})
    run_until(run)
    return run, system_queryset(StepRun).get(run=run, node_key="review")


def questions(step):
    return list(system_queryset(Decision).filter(step_run=step).order_by("pk"))


def answer(decision, person, key="approve"):
    return decide(decision, actor=person, chosen=[key])


def test_ask_answer_then_apply_as_run_actor(review):
    actor, people, sent, question = review
    run, step = start_review(review)
    assert (step.status, step.waiting_kind, step.state) == ("waiting", "decision", {"value": 17})
    decision = questions(step)[0]
    before = question.review_subject.name
    answer(decision, people[0])
    assert system_queryset(StepRun).get(pk=step.pk).status == "ready"
    question.review_subject.refresh_from_db()
    assert question.review_subject.name == before
    run_until(run)
    assert run.status == "succeeded" and run.outcome == "approved"
    question.review_subject.refresh_from_db()
    assert question.review_subject.name == "Applied"
    assert run.output == {"decisions": [public_id_of(decision)], "outcomes": ["approved"]}
    assert any(name == "workflows.execute" for name, _ in sent)


def test_wait_until_every_independent_question_is_answered(review):
    _actor, people, _, _ = review
    run, step = start_review(review, input={"two": True})
    first, second = questions(step)
    answer(first, people[0])
    assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
    assert runner.wake_decisions() == 0
    answer(second, people[1], "reject")
    run_until(run)
    assert run.status == "succeeded" and run.outcome == "done"
    assert run.output["outcomes"] == ["approved", "rejected"]
    assert not system_queryset(Decision).open().exists()


def test_multiple_choices_receive_the_set_and_route_done(review):
    _, people, _, _ = review
    run, step = start_review(review, input={"multiple": True})
    decision = questions(step)[0]
    Decision.objects.decide(decision, actor=people[0], chosen=["reject", "approve"])
    run_until(run)
    assert run.outcome == "done" and run.output["outcomes"] == ["approved", "rejected"]


def test_apply_failure_rolls_back_record_writes_and_retains_answer(review, register_step):
    actor, people, _, question = review
    run, step = start_review(review, input={"invalid": True})
    original_name = question.review_subject.name
    decision = questions(step)[0]
    answer(decision, people[0])
    run_until(run)
    question.review_subject.refresh_from_db()
    assert run.status == "failed" and question.review_subject.name == original_name
    assert system_queryset(Decision).get(pk=decision.pk).verdict == ["approve"]
    assert system_queryset(Decision).count() == 1
    assert system_queryset(Workflow).get(pk=run.version.workflow_id).name != "Rolled back"
    assert "Application unavailable" in system_queryset(StepAttempt).latest("pk").error

    class AvailableQuestion(question):
        def continue_with(self, ctx, decisions, outcomes):
            return DecisionStep.continue_with(self, ctx, decisions, outcomes)

    register_step(AvailableQuestion)
    StepRun.objects.retry_step(step, actor=actor)
    run_until(run)
    assert run.status == "succeeded" and system_queryset(Decision).count() == 1


def test_sweep_recovers_missed_answer_notification(review, monkeypatch):
    _, people, _, _ = review
    run, step = start_review(review)
    with monkeypatch.context() as patch:
        patch.setattr(type(runner), "wake_decisions", lambda *args, **kwargs: 0)
        answer(questions(step)[0], people[0])
    assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
    assert runner.wake_decisions() == 1
    run_until(run)
    assert run.status == "succeeded"


@pytest.mark.parametrize("answered", [0, 1, 2])
def test_cancel_keeps_independent_open_questions(review, answered):
    actor, people, _, _ = review
    run, step = start_review(review, input={"two": True})
    decisions = questions(step)
    for decision, person in zip(decisions[:answered], people, strict=False):
        answer(decision, person)
    assert WorkflowRun.objects.cancel(run, actor=actor).canceled
    assert [decision.is_open for decision in questions(step)] == [False] * answered + [True] * (2 - answered)
    assert runner.wake_decisions() == 0


def test_later_step_loads_only_this_runs_decision(review, register_step):
    _, people, _, _ = review

    class UseAnswer(Step[None, None, None]):
        key = "use_answer"

        def run(self, ctx):
            decision = ctx.decision(ctx.input["decisions"][0])
            return ctx.done({"answered_by": public_id_of(decision.answered_by), "verdict": decision.verdict})

    register_step(UseAnswer)
    run, step = start_review(
        review,
        graph={
            "nodes": {"review": {"step": "question", "next": {"approved": "use"}}, "use": {"step": UseAnswer.key}},
            "results": [{"from": "use"}],
        },
    )
    answer(questions(step)[0], people[0])
    run_until(run)
    assert run.output == {"answered_by": public_id_of(people[0]), "verdict": ["approve"]}


def test_public_record_method_is_called_by_the_asking_owner(review):
    actor, people, _, question = review
    record = vault_for(actor, name="Temporary")
    write_relationships([RelationshipTuple(to_object_ref(record), "viewer", to_subject_ref(people[0]))])
    decision = Decision.objects.ask(
        DecisionRequest(
            kind="remove_record",
            records=(record,),
            assignees=(people[0],),
            proposal=DecisionProposal(
                alternatives=[
                    {
                        "key": "remove",
                        "label": "Remove record",
                        "outcome": "removed",
                        "actions": {public_id_of(record): {"record": {"call": "delete"}}},
                    }
                ]
            ),
        ),
        actor=actor,
    )
    answer(decision, people[0], "remove")
    assert system_queryset(type(record)).filter(pk=record.pk).exists()
    assert apply_proposals([system_queryset(Decision).get(pk=decision.pk)], actor=actor) == {"removed"}
    assert not system_queryset(type(record)).filter(pk=record.pk).exists()


def test_application_uses_owner_write_permission(review):
    actor, people, _, question = review
    run, step = start_review(review)
    decision = answer(questions(step)[0], people[0])
    with pytest.raises(PermissionDenied):
        apply_proposals([decision], actor=people[0])
    question.review_subject.refresh_from_db()
    assert question.review_subject.name != "Applied"
