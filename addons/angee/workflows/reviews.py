"""Decision-backed steps: one ask/apply body and one retained group per round."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import connection, transaction
from pydantic import BaseModel, ConfigDict, Field
from rebac import system_context

from angee.base.identity import instance_from_public_id, public_id_of
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.decisions.contracts import DEFAULT_REQUESTER, DecisionContext, DecisionRequest
from angee.decisions.exceptions import ResolverAuthorityError
from angee.decisions.forms import Action, resolve_action
from angee.decisions.managers import ResolvedDecision
from angee.decisions.states import ClosedReason
from angee.workflows.states import Outcome
from angee.workflows.steps import Ask, Done, NextPage, RetryPolicy, Settlement, Step, StepMode, Wait


class ReviewStep[I, O, C, B](Step[I, O, C]):
    """Ask once, then apply settled answers as the run actor in DATABASE mode.

    Each settled entry carries its decision, parsed action, resolver and typed
    basis. Rejected application or lost resolver authority rolls back and asks
    every seat again. Other failures and operator retries retain the group.
    ``review_round`` in state belongs to this step and survives waits and pages.
    """

    basis_model: ClassVar[Any] = None
    _model_parameters = (*Step._model_parameters, "basis_model")
    actions: ClassVar[tuple[type[Action], ...]] = ()
    outcomes: ClassVar[dict[Outcome, str]] = {}
    empty_outcomes = Step.empty_outcomes | {str(ClosedReason.EXPIRED), str(ClosedReason.SUPERSEDED)}
    max_rounds: ClassVar[int] = 3
    retry = RetryPolicy(max_attempts=3, backoff=timedelta(seconds=1))

    @classmethod
    def resolution(cls, decision_ref: str, *, run: Any, actor: Any) -> ResolvedDecision:
        """Decode a retained review once, then let decisions revalidate its answer."""
        model = apps.get_model("decisions", "Decision")
        decision = instance_from_public_id(model, decision_ref, queryset=read_scoped_queryset(model, actor))
        if decision is None:
            raise PermissionDenied("The decision is absent or inaccessible.")
        step_model = apps.get_model("workflows", "StepRun")
        group_model = apps.get_model("decisions", "DecisionGroup")
        quote = connection.ops.quote_name
        step_table, group_table = quote(step_model._meta.db_table), quote(group_model._meta.db_table)
        step_pk, group_pk = quote(step_model._meta.pk.column), quote(group_model._meta.pk.column)
        run_fk = quote(step_model._meta.get_field("run").column)
        group_fk = quote(step_model._meta.get_field("decision_group").column)
        previous_fk = quote(group_model._meta.get_field("reasked_from").column)
        with system_context(reason="workflows.review_lookup"), connection.cursor() as cursor:
            cursor.execute(f"""
                WITH RECURSIVE rounds(group_id, previous_id, step_id) AS (
                    SELECT groups.{group_pk}, groups.{previous_fk}, steps.{step_pk}
                    FROM {step_table} AS steps
                    JOIN {group_table} AS groups ON groups.{group_pk} = steps.{group_fk}
                    WHERE steps.{run_fk} = %s
                    UNION ALL
                    SELECT groups.{group_pk}, groups.{previous_fk}, rounds.step_id
                    FROM {group_table} AS groups
                    JOIN rounds ON groups.{group_pk} = rounds.previous_id
                )
                SELECT step_id FROM rounds WHERE group_id = %s LIMIT 1
            """, [run.pk, decision.group_id])
            row = cursor.fetchone()
        source = (system_queryset(step_model).select_related("run__version").filter(pk=row[0]).first()
                  if row is not None else None)
        if source is None or not issubclass(source.step, cls):
            raise ValidationError("The decision has no retained review in this run.")
        step = source.step
        config = step.parse_config(source.run.version.definition.node(source.node_key).config)
        return model.objects.resolution(
            decision.pk, actor=actor, actions=step.actions_for(config), basis_model=step.basis_model,
        )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.mode != StepMode.DATABASE:
            raise ImproperlyConfigured("Review steps require DATABASE mode.")
        if cls.max_rounds < 1:
            raise ImproperlyConfigured("Review max_rounds must be positive.")

    @classmethod
    def actions_for(cls, config: Any) -> tuple[type[Action], ...]:
        """Return the actions this review can offer in its seats."""
        return cls.actions

    @classmethod
    def outcomes_for(cls, config: Any) -> dict[Outcome, str]:
        """Project each action's declared outcome, plus unanswered closures."""
        outcomes = dict(cls.outcomes)
        for action in cls.actions_for(config):
            outcome = cls.parse_value(action.outcome, Outcome, "actions.outcome")
            if outcome in cls.empty_outcomes:
                raise ValidationError("Review action outcomes cannot use reserved outcomes.")
            outcomes[outcome] = action.label
        expired = cast(ClosedReason, ClosedReason.EXPIRED)
        superseded = cast(ClosedReason, ClosedReason.SUPERSEDED)
        return {**outcomes, expired: expired.label, superseded: superseded.label}

    def run(self, ctx: Any) -> Settlement:
        """Dispatch from the retained group, never a separately persisted phase."""
        if ctx.step_run.decision_group_id is None:
            result = self.ask(ctx)
            if isinstance(result, Ask):
                result = replace(result, state={**ctx.state, **(result.state or {}), "review_round": 1})
            return result
        group = ctx.step_run.decision_group
        if group.settled_at is None:
            raise ValidationError("The decision group is still open.")
        if outcome := group.outcome:
            if outcome == ClosedReason.CANCELED:
                return ctx.fail("The decision group was canceled.")
            return Done(outcome=str(ClosedReason.EXPIRED) if outcome == ClosedReason.INVALID_ATTEMPTS else outcome)
        round_number = ctx.state["review_round"]
        artifact_count = len(ctx.pending_artifacts)
        try:
            with transaction.atomic():
                settled = apps.get_model("decisions", "Decision").objects.resolutions(
                    group.pk, actor=ctx.actor, actions=self.actions_for(ctx.config), basis_model=self.basis_model,
                )
                result = self.apply(ctx, settled)
        except (ValidationError, ResolverAuthorityError) as error:
            del ctx.pending_artifacts[artifact_count:]
            if round_number >= self.max_rounds:
                raise
            errors = ValidationError(ValidationError(error).update_error_dict({})).message_dict
            return Ask(group_id=group.pk, errors=errors, state={**ctx.state, "review_round": round_number + 1})
        if isinstance(result, (Wait, NextPage)):
            result = replace(result, state={**(result.state or {}), "review_round": round_number})
        return result

    def ask(self, ctx: Any) -> Settlement:
        """Return ctx.ask requests or ctx.done to skip the review."""
        raise NotImplementedError

    def apply(self, ctx: Any, settled: list[ResolvedDecision]) -> Settlement:
        """Apply revalidated answers; each entry owns its resolver and frozen basis."""
        raise NotImplementedError


class ReviewSeat(BaseModel):
    """No-code seat configuration using public user ids and registered action values."""

    model_config = ConfigDict(extra="forbid")
    kind: str
    assignees: tuple[str, ...] = Field(min_length=1)
    actions: tuple[str, ...] = Field(min_length=1)
    requester: str | None = None
    basis: dict[str, Any] = {}
    context: DecisionContext = Field(default_factory=DecisionContext)
    initial: dict[str, dict[str, Any]] = {}
    refine: dict[str, dict[str, dict[str, Any]]] = {}
    supersede: bool = False
    deadline: timedelta | None = Field(default=None, gt=timedelta())


class ReviewConfig(BaseModel):
    """Seats and settlement policy for the configured review implementation."""

    model_config = ConfigDict(extra="forbid")
    seats: tuple[ReviewSeat, ...] = Field(min_length=1)
    policy: str = "first"


class Review(ReviewStep[None, None, ReviewConfig, None]):
    """A configured review with the same lifecycle as authored review steps."""

    key = "review"
    label = "Review"

    @classmethod
    def parse_config(cls, value: Any) -> ReviewConfig:
        """Validate registered actions and policy with the configured seat contract."""
        config = cast(ReviewConfig, super().parse_config(value))
        try:
            cls.actions_for(config)
            apps.get_model("decisions", "DecisionGroup")._meta.get_field("policy").resolve_class(config.policy)
        except ImproperlyConfigured as error:
            raise ValidationError({"config": str(error)}) from error
        return config

    @classmethod
    def actions_for(cls, config: ReviewConfig) -> tuple[type[Action], ...]:
        """Resolve the declared action catalogue through the shared registry."""
        return tuple(resolve_action(value) for value in dict.fromkeys(
            value for seat in config.seats for value in seat.actions
        ))

    @classmethod
    def outcomes_for(cls, config: ReviewConfig) -> dict[Outcome, str]:
        """All-seat reviews expose disagreement as an explicit graph branch."""
        outcomes = super().outcomes_for(config)
        if "disputed" in outcomes:
            raise ValidationError("The configured review reserves disputed for differing answers.")
        return {**outcomes, "disputed": "Disputed"} if config.policy == "all" else outcomes

    @classmethod
    def required_outcomes(cls, config: ReviewConfig) -> set[Outcome]:
        """A disagreement under all must be handled by the authored graph."""
        return {"disputed"} if (
            config.policy == "all" and len(config.seats) > 1 and len(cls.actions_for(config)) > 1
        ) else set()

    def ask(self, ctx: Any) -> Ask:
        """Resolve participants under the run actor and freeze each declared seat."""
        user = apps.get_model("iam", "User")
        return ctx.ask(*(DecisionRequest(
            kind=seat.kind, subject=ctx.subject,
            assignees=tuple(ctx.load(user, value) for value in seat.assignees),
            actions=tuple(resolve_action(value) for value in seat.actions),
            requester=(DEFAULT_REQUESTER if "requester" not in seat.model_fields_set else
                       None if seat.requester is None else ctx.load(user, seat.requester)),
            basis=seat.basis, context=seat.context, initial=seat.initial, refine=seat.refine,
            supersede=seat.supersede,
            expires_at=ctx.now + seat.deadline if seat.deadline is not None else None,
        ) for seat in ctx.config.seats), policy=ctx.config.policy)

    def apply(self, ctx: Any, settled: list[ResolvedDecision]) -> Settlement:
        """Route by the shared action; differing all-seat answers are disputed."""
        answered = [answer for answer in settled if answer.action is not None]
        if not answered:
            return ctx.done(outcome="expired")
        output = {"decisions": [public_id_of(answer.decision) for answer in answered]}
        actions = {answer.action.key: answer.action for answer in answered if answer.action is not None}
        outcome = "disputed" if len(actions) > 1 else next(iter(actions.values())).outcome
        return ctx.done(output, outcome=outcome)
