"""A run wait composes actor-scoped reads and the settlement-owned wake seam."""

from dataclasses import dataclass, replace
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict

from angee.base.jsonschema import union_schema
from angee.workflows.states import Outcome, WaitingKind
from angee.workflows.steps import Done, Step, _Settlement


class AwaitRunInput(BaseModel):
    """The public identity of the actor-readable run to await."""

    model_config = ConfigDict(extra="forbid")
    run_id: str


class AwaitRunConfig(BaseModel):
    """The expected workflow and its frozen outcome-to-output-schema contract."""

    model_config = ConfigDict(extra="forbid")
    expects: str
    outcomes: dict[Outcome, dict[str, Any]]


@dataclass(frozen=True)
class _AwaitedRun(_Settlement):
    """Retain an observed child in either its waiting or terminal settlement."""

    run_id: int = 0
    completed: bool = False
    output: Any = None
    outcome: str = ""

    def check(self, step: type[Step], *, config: Any = None) -> _AwaitedRun:
        if not self.completed:
            return self
        if self.outcome not in config.outcomes:
            raise ValidationError(
                f"Awaited workflow {config.expects!r} returned outcome {self.outcome!r} "
                "outside this published await contract."
            )
        checked = Done(output=self.output, outcome=self.outcome).check(step, config=config)
        return replace(self, output=checked.output, outcome=checked.outcome)

    def admit(self, ctx: Any) -> _AwaitedRun:
        """Record the target through the same fence for waiting and completed observations."""
        type(ctx.step_run).objects.record_await(ctx.step_run, run_id=self.run_id)
        return self

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        if self.completed:
            return Done(output=self.output, outcome=self.outcome).transition(rows, step_run, attempt)
        return rows.to_waiting(kind=WaitingKind.RUN)


class AwaitRun(Step[AwaitRunInput, Any, AwaitRunConfig]):
    """Return a child's terminal outcome/output, or wait for its terminal wake."""

    key = "await_run"
    label = "Await run"
    empty_outcomes = frozenset()

    @classmethod
    def outcomes_for(cls, config: AwaitRunConfig) -> dict[Outcome, str]:
        """Expose only the outcomes retained in this parent version."""
        return {outcome: outcome.replace("_", " ").capitalize()
                for outcome in config.outcomes}

    @classmethod
    def required_outcomes(cls, config: AwaitRunConfig) -> set[Outcome]:
        """Every awaited terminal outcome needs an authored route."""
        return set(cls.outcomes_for(config))

    @classmethod
    def output_schema(cls, *, config: Any = None, outcomes: set[str] | None = None) -> dict[str, Any]:
        """Project only the routed outcomes when checking a successor's input."""
        if config is None:
            return {}
        schemas = config.outcomes
        return union_schema(*(schema for outcome, schema in schemas.items() if outcomes is None or outcome in outcomes))

    def run(self, ctx: Any) -> _Settlement:
        """Load through the actor's read rule; terminal rows require no child lock."""
        child = ctx.load(apps.get_model("workflows", "WorkflowRun"), ctx.input.run_id)
        child.check_await(ctx.run, expects=ctx.config.expects)
        return _AwaitedRun(run_id=child.pk, completed=child.is_terminal,
                          output=child.output, outcome=child.outcome)
