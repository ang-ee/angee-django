"""Review bodies compose decision admission, authority and retained answers."""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Now
from django.utils import timezone
from pydantic import BaseModel, Field, field_validator
from rebac import actor_context, system_context
from rebac.actors import is_sudo

from angee.base.identity import public_id_of
from angee.base.jsonschema import validator
from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionRequest
from angee.decisions.exceptions import RetryableDecisionError
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.workflows.definition import Definition
from angee.workflows.reviews import Review, ReviewStep
from angee.workflows.states import RunStatus
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import decide, load_workflow, run_until
from angee.workflows.testing.models import (
    Decision,
    DecisionGroup,
    StepArtifact,
    StepAttempt,
    StepRun,
    Workflow,
    WorkflowRun,
)


class Approve(Action, value="approve", label="Approve", verdict=Verdict.COMPLETED, outcome="approved"):
    """A typed positive answer with one defaulted field."""

    note: str = "Accepted"


class Reject(Action, value="reject", label="Reject", verdict=Verdict.REJECTED, outcome="rejected"):
    """A negative answer requiring a reason."""

    reason: str = Field(min_length=3)


class Basis(BaseModel):
    """The assessed value is retained independently of later inputs."""

    value: int


class ReviewPrefix(Step[None, None, None]):
    """Give a review a nonzero rank without changing its input contract."""

    key = "review_prefix"

    def run(self, ctx):
        return ctx.done(ctx.input)


@pytest.fixture
def review(execution, register_step):
    """Register a real review body and two independent assignees."""
    actor, sent = execution
    with system_context(reason="review participants"):
        people = tuple(get_user_model().objects.create_user(username=f"reviewer-{index}") for index in range(2))

    class Question(ReviewStep[None, None, None, Basis]):
        key = "question"
        actions = (Approve, Reject)
        review_subject = None

        def ask(self, ctx):
            seats = people if ctx.input.get("two") else people[:1]
            return ctx.ask(*(DecisionRequest(
                kind="question", subject=self.review_subject, assignees=(person,), actions=self.actions,
                basis=Basis(value=17),
            ) for person in seats), policy=ctx.input.get("policy", "first"))

        def apply(self, ctx, settled):
            assert not is_sudo()
            assert ctx.actor.pk == ctx.run.run_as_id
            assert all(answer.basis.value == 17 for answer in settled)
            Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Applied")
            if ctx.input.get("reject_rounds", 0) >= ctx.state["review_round"]:
                ctx.artifact(ctx.run, label="Discarded apply evidence")
                raise ValidationError({"note": "Please revise the answer."})
            if ctx.input.get("wait") and not ctx.state.get("waited"):
                return ctx.wait(until=timezone.now() + timedelta(days=1), state={"waited": True})
            if ctx.input.get("fail"):
                return ctx.fail("Application failed.")
            answered = [answer for answer in settled if answer.action]
            return ctx.done({
                "decisions": [public_id_of(answer.decision) for answer in answered],
                "resolvers": [public_id_of(answer.resolver) for answer in answered],
            }, outcome=answered[0].action.outcome)

    register_step(Question)
    return actor, people, sent, Question


def start_review(review, *, input=None, graph=None):
    """Reach a waiting review through the public engine, never manufactured rows."""
    actor, _people, _sent, _question = review
    workflow = load_workflow(graph or {
        "nodes": {"review": {"step": "question"}},
        "results": [{"from": "review", "when": [outcome], "as": outcome}
                    for outcome in ("approved", "rejected", "expired", "superseded")],
    }, actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input=input or {})
    run_until(run)
    return run, system_queryset(StepRun).get(run=run, node_key="review")


def seats(step):
    """Read retained seats in their authored order for assertions and submissions."""
    return list(system_queryset(Decision).filter(group_id=step.decision_group_id).order_by("index"))


def answer(decision, person, action="approve"):
    """Submit via the decisions owner with the currently retained revision."""
    return decide(
        decision, actor=person, action=action,
        values={} if action == "approve" else {"reason": "Needs revision"},
    )


def test_ask_wait_decide_enqueues_then_apply(review):
    actor, people, sent, _question = review
    run, step = start_review(review)
    assert (step.status, step.waiting_kind, step.state) == ("waiting", "decision", {"review_round": 1})
    decision = seats(step)[0]
    assert decision.basis == {"value": 17} and decision.requester_id == actor.pk
    sent.clear()
    answer(decision, people[0])
    assert system_queryset(StepRun).get(pk=step.pk).status == "ready"
    assert [payload["kwargs"]["step_run_id"] for name, payload in sent if name == "workflows.execute"] == [step.pk]
    assert system_queryset(Workflow).get(pk=run.version.workflow_id).name != "Applied"
    assert not system_queryset(StepArtifact).filter(step_run=step).exists()
    run_until(run)
    assert run.status == RunStatus.SUCCEEDED and run.outcome == "approved"
    assert run.output["resolvers"] == [public_id_of(people[0])]
    assert system_queryset(StepAttempt).filter(step_run=step, result="succeeded").count() == 2


def test_all_waits_for_each_resolver(review):
    _actor, people, _sent, _question = review
    run, step = start_review(review, input={"two": True, "policy": "all"})
    first, second = seats(step)
    answer(first, people[0])
    assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
    answer(second, people[1])
    run_until(run)
    assert run.status == "succeeded" and run.output["resolvers"] == [public_id_of(person) for person in people]


def test_requesterless_first_seat_can_be_answered_by_run_actor(review, register_step):
    actor, people, _sent, question = review
    requester = people[0]

    class DeferredQuestion(question):
        def ask(self, ctx):
            original = super().ask(ctx)
            return replace(original, requests=(
                replace(original.requests[0], assignees=(people[1],)),
                replace(original.requests[0], assignees=(requester,), requester=None, actions=(Reject,)),
            ))

        def apply(self, ctx, settled):
            answered = next(answer for answer in settled if answer.action is not None)
            return ctx.done(outcome=answered.action.outcome)

    register_step(DeferredQuestion)
    workflow = load_workflow({
        "nodes": {"review": {"step": "question"}}, "results": [{"from": "review"}],
    }, actor=actor)
    workflow.with_actor(actor).grant_record_access("starter", requester)
    run = WorkflowRun.objects.start(workflow, actor=requester)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    first, second = seats(step)
    assert first.requester_id == requester.pk and second.requester_id is None
    with pytest.raises(PermissionDenied):
        answer(first, requester)
    answer(second, requester, "reject")
    run_until(run)
    assert run.outcome == "rejected"
    assert system_queryset(Decision).get(pk=first.pk).closed_reason == "sibling_settled"


def test_rejected_apply_reasks_every_frozen_seat_and_rolls_back(review, register_step):
    actor, people, _sent, _question = review
    register_step(ReviewPrefix)
    run, step = start_review(review, input={"two": True, "reject_rounds": 1}, graph={"nodes": {
        "begin": {"step": ReviewPrefix.key, "next": {"done": "review"}},
        "review": {"step": "question"},
    }, "results": [{"from": "review"}]})
    assert step.rank == 1
    old_group = step.decision_group_id
    original = seats(step)
    answer(original[0], people[0])
    run_until(run)
    step = system_queryset(StepRun).get(pk=step.pk)
    assert step.status == "waiting" and step.decision_group_id != old_group
    assert step.rank == 1
    assert step.state == {"review_round": 2}
    assert system_queryset(Workflow).get(pk=run.version.workflow_id).name != "Applied"
    assert not system_queryset(StepArtifact).filter(step_run=step).exists()
    replacement = seats(step)
    assert len(replacement) == 2 and all(seat.is_open for seat in replacement)
    assert all(seat.errors == {"note": ["Please revise the answer."]} for seat in replacement)
    assert [seat.form_schema for seat in replacement] == [seat.form_schema for seat in original]
    assert [seat.requester_id for seat in replacement] == [actor.pk, actor.pk]
    assert system_queryset(Decision).get(pk=original[0].pk).resolution == {"action": "approve", "note": "Accepted"}
    answer(replacement[1], people[1])
    run_until(run)
    assert run.status == "succeeded"
    assert system_queryset(StepRun).get(pk=step.pk).rank == 1


def test_review_round_limit_fails_with_actual_cause(review):
    _actor, people, _sent, question = review
    run, step = start_review(review, input={"reject_rounds": 10})
    for _round in range(question.max_rounds):
        answer(seats(step)[0], people[0])
        run_until(run)
        step = system_queryset(StepRun).get(pk=step.pk)
    assert run.status == "failed" and step.outcome == "error"
    assert system_queryset(DecisionGroup).count() == question.max_rounds
    assert "Please revise" in system_queryset(StepAttempt).get(step_run=step, number=step.attempt).error


@pytest.mark.parametrize("closure", ["expired", "superseded"])
def test_unanswered_outcomes_are_owned_by_decisions(review, closure):
    actor, _people, _sent, question = review
    if closure == "superseded":
        subject = load_workflow({"nodes": {"entry": {"step": "question"}}}, key="review_subject", actor=actor)
        subject.with_actor(actor).grant_record_access("starter", _people[0])
        question.review_subject = subject
    run, step = start_review(review)
    decision = seats(step)[0]
    if closure == "expired":
        with system_context(reason="elapsed decision deadline"):
            Decision.objects.filter(pk=decision.pk).owner_update(expires_at=Now() - timedelta(seconds=1))
        assert Decision.objects.expire_due() == 1
    else:
        Decision.objects.admit_group([DecisionRequest(
            kind="question", subject=subject, assignees=(_people[0],), actions=question.actions,
            supersede=True,
        )], actor=actor)
    run_until(run)
    assert run.status == "succeeded" and run.outcome == closure and run.output == {}


def test_sweep_recovers_a_missed_settlement_signal(review, monkeypatch):
    _actor, people, _sent, _question = review
    run, step = start_review(review)
    with monkeypatch.context() as patch:
        patch.setattr("angee.workflows.apps.wake_review", lambda *args, **kwargs: None)
        patch.setattr(type(StepRun.objects), "wake_decisions", lambda *args, **kwargs: 0)
        answer(seats(step)[0], people[0])
    assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
    assert StepRun.objects.wake_decisions() == 1
    run_until(run)
    assert run.status == "succeeded"


def test_apply_wait_and_operator_retry_keep_the_settled_group(review):
    actor, people, _sent, _question = review
    run, step = start_review(review, input={"wait": True})
    answer(seats(step)[0], people[0])
    run_until(run)
    retained = system_queryset(StepRun).get(pk=step.pk)
    assert retained.waiting_kind == "time" and retained.decision_group_id == step.decision_group_id
    assert retained.state == {"review_round": 1, "waited": True}
    with system_context(reason="advance review reconciliation time"):
        StepRun.objects.filter(pk=step.pk).update(wake_at=Now() - timedelta(seconds=1))
    StepRun.objects.wake()
    run_until(run)
    assert run.status == "succeeded" and system_queryset(DecisionGroup).count() == 1


@pytest.mark.parametrize("inactive", [False, True])
def test_apply_rechecks_resolver_authority_and_reasks(review, register_step, inactive):
    _actor, people, _sent, _question = review

    class SharedQuestion(_question):
        def ask(self, ctx):
            original = super().ask(ctx)
            return replace(original, requests=(replace(original.requests[0], assignees=people),))

    register_step(SharedQuestion)
    run, step = start_review(review)
    decision = seats(step)[0]
    answer(decision, people[0])
    with system_context(reason="revoke resolver authority"):
        if inactive:
            get_user_model().objects.filter(pk=people[0].pk).update(is_active=False)
        else:
            decision.assignees.remove(people[0])
    run_until(run)
    current = system_queryset(StepRun).get(pk=step.pk)
    assert current.status == "waiting" and current.state == {"review_round": 2}
    assert current.decision_group_id != step.decision_group_id
    assert seats(current)[0].errors["__all__"]
    assert system_queryset(Workflow).get(pk=run.version.workflow_id).name != "Applied"
    answer(seats(current)[0], people[1])
    run_until(run)
    assert run.status == "succeeded"


def test_unanswered_closure_does_not_consume_a_revoked_sibling_answer(review):
    _actor, people, _sent, _question = review
    run, step = start_review(review, input={"two": True, "policy": "all"})
    first, second = seats(step)
    answer(first, people[0])
    with system_context(reason="revoke answered seat then expire the unanswered one"):
        get_user_model().objects.filter(pk=people[0].pk).update(is_active=False)
        Decision.objects.filter(pk=second.pk).owner_update(expires_at=Now() - timedelta(seconds=1))
    assert Decision.objects.expire_due() == 1
    run_until(run)
    assert run.status == "succeeded" and run.outcome == "expired"


def test_failed_apply_operator_retry_keeps_answers(review, register_step):
    actor, people, _sent, question = review

    class FailingQuestion(question):
        def apply(self, ctx, settled):
            return ctx.fail("Application unavailable.")

    register_step(FailingQuestion)
    register_step(ReviewPrefix)
    run, step = start_review(review, graph={"nodes": {
        "begin": {"step": ReviewPrefix.key, "next": {"done": "review"}},
        "review": {"step": "question"},
    }, "results": [{"from": "review"}]})
    assert step.rank == 1
    decision = seats(step)[0]
    answer(decision, people[0])
    run_until(run)
    assert run.status == "failed"
    register_step(question)
    StepRun.objects.retry_step(step, actor=actor)
    assert system_queryset(StepRun).get(pk=step.pk).rank == 1
    run_until(run)
    assert run.status == "succeeded" and run.outcome == "approved"
    assert system_queryset(DecisionGroup).count() == 1
    assert system_queryset(StepRun).get(pk=step.pk).decision_group_id == decision.group_id
    assert system_queryset(StepRun).get(pk=step.pk).rank == 1


def test_evidence_readability_failure_rolls_back_ask_with_cause(review, register_step):
    actor, _people, _sent, question = review
    hidden = load_workflow({"nodes": {"entry": {"step": "question"}}}, key="private_subject", actor=actor)

    class PrivateQuestion(question):
        review_subject = hidden

        def ask(self, ctx):
            Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Uncommitted ask")
            return super().ask(ctx)

    register_step(PrivateQuestion)
    run, step = start_review(review)
    assert run.status == "failed" and step.decision_group_id is None
    assert not system_queryset(DecisionGroup).exists()
    assert "PermissionDenied" in system_queryset(StepAttempt).get(step_run=step).stacktrace
    assert system_queryset(Workflow).get(pk=run.version.workflow_id).name != "Uncommitted ask"


def test_later_step_reads_locked_typed_resolution(review, register_step):
    _actor, people, _sent, _question = review

    class UseAnswer(Step[None, None, None]):
        key = "use_answer"

        def run(self, ctx):
            resolution = ctx.resolution(ctx.input["decisions"][0])
            assert isinstance(resolution.action, Approve) and isinstance(resolution.basis, Basis)
            return ctx.done({"resolver": public_id_of(resolution.resolver), "value": resolution.basis.value})

    register_step(UseAnswer)
    run, step = start_review(review, graph={"nodes": {
        "review": {"step": "question", "next": {"approved": "use"}},
        "use": {"step": "use_answer"},
    }, "results": [{"from": "use"}]})
    answer(seats(step)[0], people[0])
    run_until(run)
    assert run.output == {"resolver": public_id_of(people[0]), "value": 17}


def test_single_consumer_and_delete_protection(review):
    actor, people, _sent, _question = review
    run, step = start_review(review)
    group = system_queryset(DecisionGroup).get(pk=step.decision_group_id)
    assert not group.is_deletable
    other = WorkflowRun.objects.start(run.version.workflow, actor=actor)
    with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="single waiter constraint"):
        StepRun.objects.filter(run=other).update(decision_group_id=group.pk)
    answer(seats(step)[0], people[0])
    run_until(run)
    group = system_queryset(DecisionGroup).get(pk=group.pk)
    assert not group.is_deletable
    with pytest.raises(ProtectedError), actor_context(actor):
        group.delete()


def test_cancel_closes_pending_review_seats(review):
    actor, _people, _sent, _question = review
    run, step = start_review(review, input={"two": True, "policy": "all"})
    result = WorkflowRun.objects.cancel(run, actor=actor)
    assert result.canceled and result.steps == 1
    assert all(seat.closed_reason == "canceled" for seat in seats(step))
    assert system_queryset(StepRun).get(pk=step.pk).status == "canceled"
    assert StepRun.objects.wake_decisions() == 0


@pytest.mark.parametrize("policy,actions,outcome", [
    ("first", ["approve"], "approved"),
    ("all", ["approve", "approve"], "approved"),
    ("all", ["approve", "reject"], "disputed"),
])
def test_configured_review_routes_shared_action_or_disputed(review, settings, register_step, policy, actions, outcome):
    actor, people, _sent, _question = review
    settings.ANGEE_DECISION_ACTION_CLASSES = {
        "approve": "tests.test_workflows_review.Approve", "reject": "tests.test_workflows_review.Reject",
    }
    register_step(Review)

    class Finished(Step[None, None, None]):
        key = "finish_dispute"

        def run(self, ctx):
            return ctx.done(ctx.input)

    register_step(Finished)
    node = {"step": "review", "config": {"policy": policy, "seats": [
        {"kind": "configured", "assignees": [public_id_of(person)], "actions": ["approve", "reject"]}
        for person in people
    ]}}
    graph = {"nodes": {"review": node}}
    if policy == "all":
        assert any(issue.code == "required_outcome" for issue in Definition.check(graph)[1])
        node["next"] = {"disputed": "dispute"}
        graph["nodes"]["dispute"] = {"step": Finished.key}
    run, step = start_review(review, graph=graph)
    for seat, person, action in zip(seats(step), people, actions, strict=False):
        answer(seat, person, action)
    run_until(run)
    assert system_queryset(StepRun).get(pk=step.pk).outcome == outcome
    assert run.status == "succeeded"


def test_basis_validation_happens_once_at_the_body_boundary(review, register_step):
    _actor, _people, _sent, question = review
    validations = []

    class CheckedBasis(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def count(cls, value):
            validations.append(value)
            return value

    class CheckedQuestion(question):
        basis_model = CheckedBasis

        def ask(self, ctx):
            raw = super().ask(ctx)
            return replace(raw, requests=(replace(raw.requests[0], basis={"value": 9}),))

    register_step(CheckedQuestion)
    run, step = start_review(review)
    assert validations == [9] and seats(step)[0].basis == {"value": 9}


def test_review_can_skip_without_admitting_a_group(review, register_step):
    _actor, _people, _sent, question = review

    class SkippedQuestion(question):
        def ask(self, ctx):
            return ctx.done(outcome="approved")

    register_step(SkippedQuestion)
    run, step = start_review(review)
    assert run.status == "succeeded" and step.decision_group_id is None
    assert not system_queryset(DecisionGroup).exists()


def test_admission_conflict_uses_the_default_review_retry_policy(review, monkeypatch):
    _actor, _people, _sent, question = review
    assert question.retry.max_attempts == 3 and question.retry.backoff == timedelta(seconds=1)

    def conflict(*args, **kwargs):
        raise RetryableDecisionError("A concurrent admission won.")

    with monkeypatch.context() as patch:
        patch.setattr(type(Decision.objects), "admit_group", conflict)
        run, step = start_review(review)
    assert step.waiting_kind == "time" and step.retries == 1 and step.decision_group_id is None
    with system_context(reason="advance admission retry deadline"):
        StepRun.objects.filter(pk=step.pk).update(wake_at=Now() - timedelta(seconds=1))
    assert StepRun.objects.wake() == 1
    run_until(run)
    retained = system_queryset(StepRun).get(pk=step.pk)
    assert retained.waiting_kind == "decision" and retained.decision_group_id is not None


def test_review_closure_edges_and_results_expose_empty_output(review, register_step):
    class Output(BaseModel):
        value: int

    class TypedQuestion(ReviewStep[None, Output, None, Basis]):
        key = "typed_question"
        actions = (Approve,)

    class NeedsValue(Step[Output, Output, None]):
        key = "needs_value"

    register_step(TypedQuestion)
    register_step(NeedsValue)
    graph = {"nodes": {
        "review": {"step": TypedQuestion.key, "next": {"expired": "next"}},
        "next": {"step": NeedsValue.key},
    }}
    assert any(issue.code == "input" for issue in Definition.check(graph)[1])
    graph = {"nodes": {"review": {"step": TypedQuestion.key}},
             "results": [{"from": "review", "when": ["expired"]}]}
    definition, issues = Definition.check(graph)
    assert not issues
    schema = validator(definition.result_schema(definition.results[0]))
    assert schema.is_valid({})
    assert not schema.is_valid({"value": 1})
    assert not schema.is_valid(None)
    graph["results"][0]["output"] = {"from": "review", "path": ["value"]}
    assert any(issue.code == "binding" for issue in Definition.check(graph)[1])


def test_reask_chain_protects_history_and_retains_operator_resolution(review, workflow_permissions):
    actor, people, _sent, question = review
    run, step = start_review(review, input={"reject_rounds": 2})
    original = seats(step)[0]
    for _round in range(2):
        answer(seats(step)[0], people[0])
        run_until(run)
        step = system_queryset(StepRun).get(pk=step.pk)
    current = system_queryset(DecisionGroup).get(pk=step.decision_group_id)
    rounds = list(current.rounds())
    assert len(rounds) == 3 and rounds[-1].pk == original.group_id
    assert all(not group.is_deletable for group in rounds)
    with pytest.raises(ProtectedError), actor_context(actor):
        rounds[-1].delete()
    operator = people[1]
    run.with_actor(actor).grant_record_access("operator", operator)
    assert DecisionGroup.objects.with_actor(operator).filter(pk__in=[group.pk for group in rounds]).count() == 3
    assert Decision.objects.with_actor(operator).filter(pk=original.pk).exists()
    retained = StepRun.objects.resolution(public_id_of(original), run=run, actor=operator)
    assert retained.resolver.pk == people[0].pk and isinstance(retained.action, Approve)
    assert isinstance(retained.basis, question.basis_model)


def test_context_resolution_rejects_another_runs_readable_answer(review, register_step):
    actor, people, _sent, _question = review
    prior, prior_step = start_review(review)
    decision = seats(prior_step)[0]
    answer(decision, people[0])
    run_until(prior)

    class ForeignAnswer(Step[None, None, None]):
        key = "foreign_answer"

        def run(self, ctx):
            ctx.resolution(public_id_of(decision))
            return ctx.done()

    register_step(ForeignAnswer)
    workflow = load_workflow({"nodes": {"read": {"step": ForeignAnswer.key}}}, key="another", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    run_until(run)
    assert run.status == "failed"
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert "no retained review in this run" in attempt.error


def test_review_preserves_ask_state_and_round_through_next_page(review, register_step):
    _actor, people, _sent, question = review
    checkpoints = []

    class PagedQuestion(question):
        def ask(self, ctx):
            return replace(super().ask(ctx), state={"authored": "as\u0000k", "basis": Basis(value=9)})

        def apply(self, ctx, settled):
            checkpoints.append(ctx.state)
            if not ctx.state.get("paged"):
                return ctx.next_page({"paged": True})
            return ctx.done(outcome="approved")

    register_step(PagedQuestion)
    run, step = start_review(review)
    assert step.state == {"authored": "ask", "basis": {"value": 9}, "review_round": 1}
    answer(seats(step)[0], people[0])
    run_until(run)
    assert run.status == "succeeded"
    assert checkpoints == [
        {"authored": "ask", "basis": {"value": 9}, "review_round": 1}, {"paged": True, "review_round": 1},
    ]
    assert system_queryset(DecisionGroup).count() == 1


def test_invalid_ask_state_fails_at_body_boundary_before_admission(review, register_step):
    _actor, _people, _sent, question = review

    class InvalidCheckpoint(question):
        def ask(self, ctx):
            Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Uncommitted checkpoint")
            return replace(super().ask(ctx), state={"value": object()})

    register_step(InvalidCheckpoint)
    run, step = start_review(review)
    assert run.status == "failed" and step.attempt == 1
    assert not system_queryset(DecisionGroup).exists()
    assert "Unable to serialize unknown type" in system_queryset(StepAttempt).get(step_run=step).error
    assert system_queryset(Workflow).get(pk=run.version.workflow_id).name != "Uncommitted checkpoint"


@pytest.mark.parametrize("action_sets,required", [
    ([["approve", "reject"]], False),
    ([["approve"], ["approve"]], False),
    ([["approve"], ["reject"]], True),
])
def test_disputed_route_required_only_when_answers_can_differ(review, settings, action_sets, required):
    _actor, people, _sent, _question = review
    settings.ANGEE_DECISION_ACTION_CLASSES = {
        "approve": "tests.test_workflows_review.Approve", "reject": "tests.test_workflows_review.Reject",
    }
    graph = {"nodes": {"review": {"step": "review", "config": {"policy": "all", "seats": [
        {"kind": "configured", "assignees": [public_id_of(people[0])], "actions": actions}
        for actions in action_sets
    ]}}}}
    issues = Definition.check(graph)[1]
    assert any(issue.code == "required_outcome" for issue in issues) is required
    assert not [issue for issue in issues if issue.code != "required_outcome"]


def test_configured_review_deadline_is_a_positive_duration(review, settings):
    _actor, people, _sent, _question = review
    settings.ANGEE_DECISION_ACTION_CLASSES = {"approve": "tests.test_workflows_review.Approve"}
    seat = {"kind": "configured", "assignees": [public_id_of(people[0])], "actions": ["approve"], "deadline": "PT5M"}
    run, step = start_review(review, graph={"nodes": {"review": {"step": "review", "config": {"seats": [seat]}}}})
    decision = seats(step)[0]
    started = system_queryset(StepAttempt).get(step_run=step).started_at
    assert decision.expires_at == started + timedelta(minutes=5)
    seat["deadline"] = "PT0S"
    with pytest.raises(ValidationError):
        Review.parse_config({"seats": [seat]})


def test_configured_review_with_no_answers_uses_unanswered_outcome(review, settings, monkeypatch):
    _actor, people, _sent, _question = review
    settings.ANGEE_DECISION_ACTION_CLASSES = {"approve": "tests.test_workflows_review.Approve"}
    run, step = start_review(review, graph={"nodes": {"review": {"step": "review", "config": {"seats": [{
        "kind": "configured", "assignees": [public_id_of(people[0])], "actions": ["approve"],
    }]}}}, "results": [{"from": "review", "when": ["expired"]}]})
    # Exercise the configured review's empty-answer contract independently of a
    # custom settlement policy's algorithm, which belongs to decisions.
    answer(seats(step)[0], people[0])
    monkeypatch.setattr(type(Decision.objects), "resolutions", lambda *args, **kwargs: [])
    run_until(run)
    assert run.status == "succeeded" and run.outcome == "expired" and run.output == {}
