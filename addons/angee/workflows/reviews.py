"""Ask-and-continue support over independent decisions."""

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field
from rebac import actor_context

from angee.base.identity import instance_from_public_id, public_id_of
from angee.base.scoping import lock_if_supported
from angee.decisions.contracts import DecisionContext, DecisionProposal, DecisionRequest
from angee.workflows.states import DONE_OUTCOME
from angee.workflows.steps import Settlement, Step, StepMode


def apply_proposals(decisions: list[Any], *, actor: Any) -> set[str]:
    """Apply chosen actions as the asking actor, in decision and proposal order."""
    outcomes = set()
    with transaction.atomic(), actor_context(actor):
        for decision in decisions:
            if decision.is_open:
                raise ValidationError("The decision is still open.")
            selected = DecisionProposal.model_validate(decision.proposal).choose(decision.verdict)
            links = {link.record_public_id: link for link in decision.records.with_actor(actor).all()}
            for alternative in selected:
                outcomes.add(alternative.outcome)
                for identity, actions in alternative.actions.items():
                    reference = links[identity].record
                    if reference is None:
                        raise ValidationError("The record for a chosen action is missing or inaccessible.")
                    record = lock_if_supported(
                        type(reference).objects.with_actor(actor).for_write().filter(pk=reference.pk)
                    ).get()
                    for name, operation in actions.fields.items():
                        field = record._meta.get_field(name)
                        value = operation.set
                        if field.many_to_one or field.one_to_one:
                            if value is not None and not isinstance(value, str):
                                raise ValidationError({name: "Use the related record public id or null."})
                            value = (
                                None
                                if value is None
                                else instance_from_public_id(
                                    field.remote_field.model,
                                    value,
                                    queryset=field.remote_field.model.objects.with_actor(actor),
                                )
                            )
                            if value is None and operation.set is not None:
                                raise ValidationError({name: "The proposed related record is inaccessible."})
                        else:
                            value = field.to_python(value)
                        setattr(record, name, value)
                    if actions.fields:
                        record.full_clean()
                        record.save(update_fields=[*actions.fields, "updated_at"])
                    if actions.record:
                        getattr(record, actions.record.call)()
    return outcomes


class DecisionStep[I, O, C](Step[I, O, C]):
    """Ask independent questions, then apply their verdicts in the body transaction."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.mode != StepMode.DATABASE:
            raise ImproperlyConfigured("Decision steps require DATABASE mode.")

    def run(self, ctx: Any) -> Settlement:
        if not ctx.step_run.decisions.exists():
            return self.ask(ctx)
        decisions = list(ctx.step_run.decisions.with_actor(ctx.actor).order_by("pk").prefetch_related("records"))
        outcomes = apply_proposals(decisions, actor=ctx.actor)
        return self.continue_with(ctx, decisions, outcomes)

    def ask(self, ctx: Any) -> Settlement:
        raise NotImplementedError

    def continue_with(self, ctx: Any, decisions: list[Any], outcomes: set[str]) -> Settlement:
        """One distinct outcome routes directly; several route done with the full set."""
        return ctx.done(
            {"decisions": [public_id_of(decision) for decision in decisions], "outcomes": sorted(outcomes)},
            outcome=next(iter(outcomes)) if len(outcomes) == 1 else DONE_OUTCOME,
        )


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1)
    assignees: tuple[str, ...] = Field(min_length=1)
    proposal: DecisionProposal
    requester: str | None = None
    context: DecisionContext = Field(default_factory=DecisionContext)


class AskDecision(DecisionStep[None, None, DecisionConfig]):
    """A configured question about the run's record."""

    key = "ask_decision"
    label = "Ask decision"

    @classmethod
    def outcomes_for(cls, config: DecisionConfig) -> dict[str, str]:
        return {
            DONE_OUTCOME: "Chosen outcomes",
            **{alternative.outcome: alternative.label for alternative in config.proposal.alternatives},
        }

    def ask(self, ctx: Any) -> Settlement:
        user = apps.get_model("iam", "User")
        return ctx.ask(
            DecisionRequest(
                kind=ctx.config.kind,
                records=(ctx.subject,),
                assignees=tuple(ctx.load(user, identity) for identity in ctx.config.assignees),
                requester=None if ctx.config.requester is None else ctx.load(user, ctx.config.requester),
                proposal=ctx.config.proposal,
                context=ctx.config.context,
            )
        )
