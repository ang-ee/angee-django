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
from angee.workflows.decision_steps import AskDecision, DecisionStep, apply_proposals
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import decide, load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun
from tests.conftest import create_platform_admin, create_user, vault_for
from tests.mtidemo.models import MtiChild, MtiParent


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
            return ctx.ask(
                DecisionRequest(
                    kind="question", records=(self.review_subject,), assignees=people,
                    proposal=DecisionProposal(
                        multiple=ctx.input.get("multiple", False), alternatives=[
                            {"key": "approve", "label": "Approve", "outcome": "approved",
                             "actions": {public_id_of(self.review_subject): {"fields": {"name": (
                                 {"choose": {}} if ctx.input.get("choose") else {"set": "Applied"}
                             )}}}},
                            {"key": "reject", "label": "Keep current record", "outcome": "rejected"},
                        ],
                    ),
                ), state={"value": 17},
            )

        def continue_with(self, ctx, decision, outcome):
            assert not is_sudo() and ctx.actor.pk == ctx.run.run_as_id
            assert ctx.state == {"value": 17}
            if ctx.input.get("invalid"):
                Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Rolled back")
                raise ValidationError("Application unavailable.")
            return super().continue_with(ctx, decision, outcome)

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
    return [system_queryset(Decision).get(pk=system_queryset(StepRun).get(pk=step.pk).decision_id)]


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
    assert run.output == {"decision": public_id_of(decision), "chosen": ["approve"]}
    assert any(name == "workflows.execute" for name, _ in sent)


@pytest.mark.parametrize("assignment_config", [{}, {"assignees": []}])
def test_configured_decision_without_assignees_waits_answers_and_applies(execution, register_step, assignment_config):
    actor, _sent = execution
    register_step(AskDecision)
    subject = vault_for(actor)
    workflow = load_workflow({
        "nodes": {"review": {"step": "ask_decision", "config": {
            "kind": "rename", **assignment_config,
            "proposal": {"alternatives": [{
                "key": "rename", "label": "Rename", "outcome": "renamed",
                "actions": {"subject": {"fields": {"name": {"set": "Applied"}}}},
            }]},
        }}},
        "results": [{"from": "review", "when": ["renamed"], "as": "renamed"}],
    }, actor=actor)
    run = start_run(workflow, actor=actor, subject=subject)
    run_until(run)
    step = system_queryset(StepRun).get(run=run, node_key="review")
    assert (step.status, step.waiting_kind) == ("waiting", "decision")
    decision = questions(step)[0]
    assert not decision.assignees.with_actor(actor).exists()
    decide(decision, actor=actor, chosen=["rename"])
    subject.refresh_from_db()
    assert subject.name != "Applied"
    run_until(run)
    subject.refresh_from_db()
    assert run.status == "succeeded" and run.outcome == "renamed" and subject.name == "Applied"
    assert run.output == {"decision": public_id_of(decision), "chosen": ["rename"]}


def test_multiple_choices_receive_the_set_and_route_done(review):
    _, people, _, _ = review
    run, step = start_review(review, input={"multiple": True})
    decision = questions(step)[0]
    Decision.objects.decide(decision, actor=people[0], chosen=["reject", "approve"])
    run_until(run)
    assert run.outcome == "done" and run.output["chosen"] == ["approve", "reject"]


def test_choose_is_applied_on_workflow_resume(review):
    _, people, _, question = review
    write_relationships([
        RelationshipTuple(to_object_ref(question.review_subject), "editor", to_subject_ref(people[0])),
    ])
    run, step = start_review(review, input={"choose": True})
    decision = questions(step)[0]
    values = {public_id_of(question.review_subject): {"name": "Answerer's value"}}
    Decision.objects.decide(decision, actor=people[0], chosen=["approve"], values=values)
    question.review_subject.refresh_from_db()
    assert question.review_subject.name != "Answerer's value"
    assert system_queryset(Decision).get(pk=decision.pk).verdict_values == values
    run_until(run)
    question.review_subject.refresh_from_db()
    assert run.status == "succeeded" and run.outcome == "approved"
    assert question.review_subject.name == "Answerer's value"


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
        def continue_with(self, ctx, decision, outcome):
            return DecisionStep.continue_with(self, ctx, decision, outcome)

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


def test_terminal_failure_withdraws_an_open_sibling_question(review, register_step):
    actor, people, _, question = review
    from tests.workflow_steps import Echo, Reject

    register_step(Echo)
    register_step(Reject)
    workflow = load_workflow({"nodes": {
        "entry": {"step": "echo", "next": {"done": ["ask", "fail"]}},
        "ask": {"step": question.key}, "fail": {"step": "reject"},
    }}, actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=question.review_subject)
    assert runner.execute(system_queryset(StepRun).get(run=run, node_key="entry").pk)
    step = system_queryset(StepRun).get(run=run, node_key="ask")
    assert runner.execute(step.pk)
    decision = questions(step)[0]
    assert decision.is_open
    assert runner.execute(system_queryset(StepRun).get(run=run, node_key="fail").pk)
    decision.refresh_from_db()
    assert decision.verdict == []
    with pytest.raises(ValidationError, match="changed"):
        decision.decide(actor=people[0], chosen=["approve"])


@pytest.mark.parametrize("answered", [False, True])
def test_cancel_withdraws_only_the_open_question(review, answered):
    actor, people, _, question = review
    run, step = start_review(review)
    if answered:
        answer(questions(step)[0], people[0])
    before = question.review_subject.name
    assert WorkflowRun.objects.cancel(run, actor=actor).canceled
    decision = questions(step)[0]
    assert not decision.is_open
    assert decision.verdict == (["approve"] if answered else [])
    assert decision.answered_by_id == (people[0].pk if answered else actor.pk)
    question.review_subject.refresh_from_db()
    assert question.review_subject.name == before
    assert runner.wake_decisions() == 0


def test_later_step_loads_only_this_runs_decision(review, register_step):
    _, people, _, _ = review

    class UseAnswer(Step[None, None, None]):
        key = "use_answer"

        def run(self, ctx):
            decision = ctx.decision(ctx.input["decision"])
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


def test_undeclared_delete_is_rejected_at_ask(review):
    actor, people, _, question = review
    with pytest.raises(ValueError, match="Undeclared decision method"):
        Decision.objects.ask(DecisionRequest(
            kind="remove_record", records=(question.review_subject,), assignees=people,
            proposal=DecisionProposal(alternatives=[{
                "key": "remove", "label": "Remove", "outcome": "removed",
                "actions": {public_id_of(question.review_subject): {"record": {"call": "delete"}}},
            }]),
        ), actor=actor)


def test_application_uses_owner_write_permission(review):
    actor, people, _, question = review
    run, step = start_review(review)
    decision = answer(questions(step)[0], people[0])
    with pytest.raises(PermissionDenied):
        apply_proposals(decision, actor=people[0])
    question.review_subject.refresh_from_db()
    assert question.review_subject.name != "Applied"


def test_concrete_action_owner_applies_child_fields_with_canonical_concern(composed_tables):
    actor = create_platform_admin("concrete-review")
    reader = create_user("concrete-reader")
    with system_context(reason="test concrete decision target"):
        record = MtiChild.objects.create(title="Parent", detail="Before")
    write_relationships([
        RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(reader)),
        RelationshipTuple(to_object_ref(record.mtiparent_ptr), "reader", to_subject_ref(reader)),
    ])
    decision = Decision.objects.ask(DecisionRequest(
        kind="confirm_detail", records=(record,), assignees=(reader,), requester=None,
        proposal=DecisionProposal(alternatives=[{
            "key": "confirm", "label": "Confirm detail", "outcome": "confirmed",
            "actions": {public_id_of(record): {"model": "mtidemo.MtiChild", "fields": {"detail": {"set": "After"}}}},
        }]),
    ), actor=actor)
    concern = decision.records.with_actor(actor).get()
    assert type(concern.record) is MtiParent
    decision = Decision.objects.decide(decision, actor=reader, chosen=["confirm"])
    with pytest.raises(PermissionDenied):
        apply_proposals(decision, actor=reader)
    record.refresh_from_db()
    assert record.detail == "Before"
    assert apply_proposals(decision, actor=actor) == "confirmed"
    record.refresh_from_db()
    assert record.detail == "After" and record.title == "Parent"


@pytest.mark.parametrize("model", ["storage.Drive", "missing.Model", "not-a-model", ""])
def test_action_model_must_share_the_concerned_identity(composed_tables, model):
    actor = create_platform_admin("invalid-concrete-review")
    with system_context(reason="test invalid decision target"):
        record = MtiChild.objects.create(detail="Before")
    with pytest.raises(ValueError):
        DecisionRequest(
            kind="confirm_detail", records=(record,), assignees=(actor,),
            proposal=DecisionProposal(alternatives=[{
                "key": "confirm", "label": "Confirm", "outcome": "confirmed",
                "actions": {public_id_of(record): {"model": model, "fields": {"detail": {"set": "After"}}}},
            }]),
        )
