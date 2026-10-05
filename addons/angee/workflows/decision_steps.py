"""Ask-and-continue support over independent decisions."""

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field
from rebac import actor_context

from angee.base.identity import public_id_of
from angee.base.scoping import lock_if_supported
from angee.decisions.contracts import DecisionContext, DecisionProposal, DecisionRequest
from angee.workflows.states import DONE_OUTCOME
from angee.workflows.steps import Settlement, Step, StepMode


def apply_proposals(decision: Any, *, actor: Any, ctx: Any = None) -> str:
    """Apply one verdict in authored order through each record's write owner."""
    outcomes = set()
    with transaction.atomic(), actor_context(actor):
        if decision.is_open:
            raise ValidationError("The decision is still open.")
        if decision.verdict == []:
            raise ValidationError("A withdrawn decision cannot be applied.")
        selected = DecisionProposal.model_validate(decision.proposal).choose(decision.verdict)
        links = {link.record_public_id: link for link in decision.records.with_actor(actor).all()}
        for alternative in selected:
            outcomes.add(alternative.outcome)
            for identity, actions in alternative.actions.items():
                link = links.get(identity)
                reference = link.record if link is not None else None
                if reference is None:
                    raise ValidationError("The record for a chosen action is missing or inaccessible.")
                record = lock_if_supported(
                    actions.target_model(reference).objects.with_actor(actor).for_write().filter(pk=reference.pk)
                ).get()
                try:
                    values = actions.resolve(record, context={"actor": actor})
                except ValueError as error:
                    raise ValidationError(str(error)) from error
                for name, value in values.items():
                    setattr(record, name, value)
                if actions.fields:
                    record.full_clean()
                    updated = [*actions.fields]
                    if any(field.name == "updated_at" for field in record._meta.fields):
                        updated.append("updated_at")
                    record.save(update_fields=updated)
                    if ctx is not None:
                        ctx.record(record, operation="changed")
                if actions.record:
                    if ctx is not None:
                        ctx.record(record, operation="called")
                    getattr(record, actions.record.call)(**actions.record.arguments)
    return next(iter(outcomes)) if len(outcomes) == 1 else DONE_OUTCOME


class DecisionStep[I, O, C](Step[I, O, C]):
    """Ask one question, then apply its verdict in the step's body transaction."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.mode != StepMode.DATABASE:
            raise ImproperlyConfigured("Decision steps require DATABASE mode.")

    def run(self, ctx: Any) -> Settlement:
        if ctx.step_run.decision_id is None:
            return self.ask(ctx)
        decision = ctx.step_run.decision.with_actor(ctx.actor)
        outcome = self.apply(ctx, decision)
        return self.continue_with(ctx, decision, outcome)

    def apply(self, ctx: Any, decision: Any) -> str:
        """Domain steps may dispatch all changes to a stronger record owner."""
        return apply_proposals(decision, actor=ctx.actor, ctx=ctx)

    def ask(self, ctx: Any) -> Settlement:
        raise NotImplementedError

    def continue_with(self, ctx: Any, decision: Any, outcome: str) -> Settlement:
        return ctx.done(
            {"decision": public_id_of(decision), "chosen": decision.verdict}, outcome=outcome,
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
        proposal = ctx.config.proposal.model_dump(mode="json")
        for alternative in proposal["alternatives"]:
            actions = alternative["actions"]
            if set(actions) - {"subject"}:
                raise ValidationError("Configured actions use the symbolic subject key.")
            alternative["actions"] = {public_id_of(ctx.subject): value for value in actions.values()}
        return ctx.ask(
            DecisionRequest(
                kind=ctx.config.kind,
                records=(ctx.subject,),
                assignees=tuple(ctx.load(user, identity) for identity in ctx.config.assignees),
                requester=None if ctx.config.requester is None else ctx.load(user, ctx.config.requester),
                proposal=DecisionProposal.model_validate(proposal),
                context=ctx.config.context,
            )
        )
