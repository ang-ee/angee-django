"""Bounded map bodies and the typed partial-result contract consumers import."""

from dataclasses import dataclass
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.experimental.missing_sentinel import MISSING

from angee.workflows.states import ERROR_OUTCOME, Outcome, WaitingKind
from angee.workflows.steps import EmptyOutput, Step, _Settlement


class MapInput(BaseModel):
    """The admitted list; the definition refines its element schema from the body."""

    model_config = ConfigDict(extra="forbid")
    items: list[Any]


class MapItem[O](BaseModel):
    """One ordered result: success carries output; failure carries a reader-facing error.

    Consumers declare ``list[MapItem[TheirOutput]]`` as their input or a field of
    it. Exactly one of output/error is present, and failed items use ``error``
    as their outcome. The absent alternative is omitted from serialized JSON.
    """

    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    outcome: Outcome
    output: O | MISSING = MISSING  # type: ignore[valid-type]
    error: str | MISSING = MISSING  # type: ignore[valid-type]

    @model_validator(mode="after")
    def check_result(self) -> Self:
        """Keep the two result alternatives exclusive and outcome-consistent."""
        if (self.output is MISSING) == (self.error is MISSING):
            raise ValueError("A map item requires exactly one of output and error.")
        if self.error is not MISSING and self.outcome != ERROR_OUTCOME:
            raise ValueError("A failed map item requires the error outcome.")
        return self


@dataclass(frozen=True)
class _MapWait(_Settlement):
    """Park the containing step while the graph planner admits its body rows."""

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        return rows.to_waiting(kind=WaitingKind.MAP)


class Map(Step[MapInput, list[MapItem[Any]], None]):
    """Run a list body, then collect it in a second ordinary fenced attempt.

    Planning and wakeup belong to Definition.ready_nodes and advance; this body
    never inserts execution rows or changes statuses. A handled item failure is
    reported as ``failed`` with partial results after every item settles.
    """

    key = "map"
    label = "Map"
    outcomes = {"done": "Done", "failed": "Failed"}

    @classmethod
    def output_schema_for(cls, body_step: type[Step]) -> dict[str, Any]:
        """Project the body type, including its declared empty-output outcomes.

        Bodies with declared empty outcomes collect ``MapItem[Output | EmptyOutput]``.
        """
        model = body_step.output_model or Any
        if body_step.output_model is not None and body_step.empty_outcomes - {ERROR_OUTCOME}:
            model = model | EmptyOutput
        return cls._schema(list[MapItem[model]], "serialization")  # type: ignore[valid-type]

    def run(self, ctx: Any) -> _Settlement:
        """Wait for bodies or return ordered typed evidence."""
        items = ctx.step_run.map_rows().collect_map(len(ctx.input.items))
        return _MapWait() if items is None else self.done(
            items, outcome="failed" if any("error" in item for item in items) else "done",
        )
