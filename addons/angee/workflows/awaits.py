"""A run wait composes actor-scoped reads and the settlement-owned wake seam."""

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Literal

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict

from angee.base.jsonschema import union_schema, validate, validator
from angee.base.scoping import system_queryset
from angee.workflows.states import Outcome, WaitingKind
from angee.workflows.steps import Done, Settlement, Step

_expecting: ContextVar[tuple[str, ...]] = ContextVar("workflow_expected_contracts", default=())


class AwaitRunInput(BaseModel):
    """The public identity of the actor-readable run to await."""

    model_config = ConfigDict(extra="forbid")
    run_id: str


class AwaitRunConfig(BaseModel):
    """The published workflow whose outcome contracts this node expects."""

    model_config = ConfigDict(extra="forbid")
    expects: str


@dataclass(frozen=True)
class AwaitedRun(Settlement):
    """Retain an observed child in either its waiting or terminal settlement."""

    kind: Literal["done", "run"] = "run"
    run_id: int = 0

    def admit(self, ctx: Any) -> AwaitedRun:
        """Record the target through the same fence for waiting and completed observations."""
        type(ctx.step_run).objects.record_await(ctx.step_run, run_id=self.run_id)
        return self

    def wait_parameters(self) -> dict[str, Any] | None:
        """Park through the same transition as the other wait settlements."""
        return {"kind": WaitingKind.RUN} if self.kind == "run" else None


class AwaitRun(Step[AwaitRunInput, Any, AwaitRunConfig]):
    """Return a child's terminal outcome/output, or wait for its terminal wake."""

    key = "await_run"
    label = "Await run"
    empty_outcomes = frozenset()

    @classmethod
    def outcomes_for(cls, config: AwaitRunConfig) -> dict[Outcome, str]:
        """Expose the expected workflow's actual terminal outcomes."""
        return {outcome: outcome.replace("_", " ").capitalize()
                for outcome in cls.expected_schemas(config)}

    @classmethod
    def expected_schemas(cls, config: AwaitRunConfig) -> dict[str, dict[str, Any]]:
        """Reject recursive publication contracts with an actionable issue."""
        if config.expects in _expecting.get():
            raise ValidationError("Awaited workflow output contracts cannot be recursive.")
        with _expecting.set((*_expecting.get(), config.expects)):
            workflow = system_queryset(apps.get_model("workflows", "Workflow")).select_related("published").filter(
                key=config.expects,
            ).first()
            if workflow is None or workflow.published_id is None:
                raise ValidationError(f"Expected workflow {config.expects!r} must exist and be published.")
            return workflow.published.definition.output_schemas

    @classmethod
    def required_outcomes(cls, config: AwaitRunConfig) -> set[Outcome]:
        """Every awaited terminal outcome needs an authored route."""
        return set(cls.outcomes_for(config))

    @classmethod
    def output_schema(cls, *, config: Any = None, outcomes: set[str] | None = None) -> dict[str, Any]:
        """Project only the routed outcomes when checking a successor's input."""
        if config is None:
            return {}
        schemas = cls.expected_schemas(config)
        return union_schema(*(schema for outcome, schema in schemas.items() if outcomes is None or outcome in outcomes))

    @classmethod
    def check(cls, settlement: Settlement, *, config: Any = None) -> Settlement:
        """Validate persisted JSON once, preserving the observed outcome and target."""
        if isinstance(settlement, AwaitedRun) and settlement.kind == "run":
            return settlement
        if isinstance(settlement, (Done, AwaitedRun)) and settlement.kind == "done":
            outcome = cls.parse_value(settlement.outcome, Outcome, "outcome")
            schemas = cls.expected_schemas(config)
            if outcome not in schemas:
                raise ValidationError(f"The expected workflow does not offer outcome {outcome!r}.")
            validate(validator(schemas[outcome]), settlement.output)
            return replace(settlement, outcome=outcome)
        return super().check(settlement, config=config)

    def run(self, ctx: Any) -> Settlement:
        """Load through the actor's read rule; terminal rows require no child lock."""
        child = ctx.load(apps.get_model("workflows", "WorkflowRun"), ctx.input.run_id)
        child.check_await(ctx.run, expects=ctx.config.expects)
        return AwaitedRun(run_id=child.pk, kind="done" if child.is_terminal else "run",
                          output=child.output, outcome=child.outcome)
