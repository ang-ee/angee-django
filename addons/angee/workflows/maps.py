"""Bounded map bodies and the typed partial-result contract consumers import."""

from dataclasses import dataclass, field
from typing import Any, Literal, Self

from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Subquery
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.experimental.missing_sentinel import MISSING

from angee.workflows.states import ERROR_OUTCOME, Outcome, StepRunStatus, WaitingKind
from angee.workflows.steps import EmptyOutput, Settlement, Step


class MapInput(BaseModel):
    """The admitted list; the definition refines its element schema from the body."""

    model_config = ConfigDict(extra="forbid")
    items: list[Any]


class MapItem[O](BaseModel):
    """One ordered result: success carries output; failure carries its attempt error.

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
        if self.outcome == ERROR_OUTCOME:
            if self.error is MISSING or self.output is not MISSING:
                raise ValueError("A failed map item requires error and no output.")
        elif self.output is MISSING or self.error is not MISSING:
            raise ValueError("A succeeded map item requires output and no error.")
        return self


@dataclass(frozen=True)
class MapWait(Settlement):
    """Park the containing step while the graph planner admits its body rows."""

    kind: Literal["map"] = field(default="map", init=False)

    def wait_parameters(self) -> dict[str, Any]:
        """Compose the same settlement-owned seam as time and decision waits."""
        return {"kind": WaitingKind.MAP}


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

        Typed review collectors use ``MapItem[ReviewOutput | EmptyOutput]``:
        an expired or superseded review has no applied output model instance.
        """
        model = body_step.output_model or Any
        if body_step.output_model is not None and body_step.empty_outcomes - {ERROR_OUTCOME}:
            model = model | EmptyOutput
        return cls._schema(list[MapItem[model]], "serialization")  # type: ignore[valid-type]

    @classmethod
    def check(cls, settlement: Settlement, *, config: Any = None) -> Settlement:
        """Check the map wait at its owner; ordinary results use Step.check once."""
        return settlement if isinstance(settlement, MapWait) else super().check(settlement, config=config)

    def run(self, ctx: Any) -> Settlement:
        """Wait for bodies or return ordered typed evidence with one error query."""
        rows = ctx.step_run.map_rows().order_by("map_index")
        attempts = ctx.step_run.attempts.model.objects.filter(step_run_id=OuterRef("pk"), number=OuterRef("attempt"))
        rows = list(rows.annotate(item_error=Subquery(attempts.values("error")[:1])))
        if len(rows) != len(ctx.input.items) or any(row.status not in StepRunStatus.terminal_values() for row in rows):
            return MapWait()
        if any(row.status not in (StepRunStatus.SUCCEEDED, StepRunStatus.FAILED) for row in rows):
            raise ValidationError("A canceled or skipped map body cannot produce a result.")
        items = [{
            "index": row.map_index, "outcome": row.outcome,
            **({"error": row.item_error or ""} if row.status == StepRunStatus.FAILED else {"output": row.output}),
        } for row in rows]
        return self.done(items, outcome="failed" if any(row.status == StepRunStatus.FAILED for row in rows) else "done")
