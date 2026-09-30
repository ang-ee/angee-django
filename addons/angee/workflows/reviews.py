"""Decision-backed steps: one ask/apply body and one retained group per round."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, ClassVar, Literal, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field

from angee.base.identity import public_id_of
from angee.decisions.contracts import DEFAULT_REQUESTER, DecisionContext, DecisionRequest
from angee.decisions.exceptions import ResolverAuthorityError
from angee.decisions.forms import Action, resolve_action
from angee.decisions.managers import ResolvedDecision
from angee.workflows.states import Outcome, WaitingKind
from angee.workflows.steps import Done, NextPage, RetryPolicy, Settlement, Step, Wait


@dataclass(frozen=True)
class Ask(Settlement):
    """Raw review requests; validation and admission run at the body boundary."""

    kind: Literal["ask"] = field(default="ask", init=False)
    requests: tuple[DecisionRequest, ...] = ()
    policy: str = "first"
    group_id: Any = None
    errors: dict[str, list[str]] = field(default_factory=dict)

    def admit(self, ctx: Any) -> Ask:
        """Admit or re-ask through decisions after settlement validation succeeds."""
        manager = apps.get_model("decisions", "Decision").objects
        if self.group_id is None:
            group = manager.admit_group(self.requests, actor=ctx.actor, policy=self.policy)
        else:
            group = manager.reask(
                self.group_id, actor=ctx.actor, actions=ctx.step.actions_for(ctx.config), errors=self.errors,
            )
        return replace(self, group_id=group.pk)

    def wait_parameters(self) -> dict[str, Any]:
        """Park on the admitted group through the fenced queryset transition."""
        return {"kind": WaitingKind.DECISION, "state": self.state, "decision_group_id": self.group_id}


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
    empty_outcomes = Step.empty_outcomes | {"expired", "superseded"}
    max_rounds: ClassVar[int] = 3
    retry = RetryPolicy(max_attempts=3, backoff=timedelta(seconds=1))

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.mode != "DATABASE":
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
        return {**outcomes, "expired": "Expired", "superseded": "Superseded"}

    @classmethod
    def check(cls, settlement: Settlement, *, config: Any = None) -> Settlement:
        """Check basis once before admission; unanswered closures have no output."""
        if isinstance(settlement, Ask):
            offered = cls.actions_for(config)
            requests = []
            for request in settlement.requests:
                if any(action not in offered for action in request.actions):
                    raise ValidationError("A seat offers an action outside this review's declaration.")
                parsed = cls.parse_value(request.basis, cls.basis_model, "basis")
                basis = cls._adapter(cls.basis_model).dump_python(parsed, mode="json", by_alias=True)
                requests.append(replace(request, basis=basis))
            return replace(settlement, requests=tuple(requests), state=cls.serialize_state(settlement.state))
        if isinstance(settlement, Done) and settlement.outcome in {"expired", "superseded"}:
            return Done(outcome=settlement.outcome)
        return super().check(settlement, config=config)

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
            if outcome == "canceled":
                return ctx.fail("The decision group was canceled.")
            return Done(outcome=outcome)
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
