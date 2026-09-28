"""Pure input/result bindings for workflow data flow."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr

from angee.base.jsonschema import schema_at
from angee.workflows.states import INPUT_SOURCE

ABSENT = object()
"""An unavailable binding source, distinct from an explicit JSON null."""


class SourceBinding(BaseModel):
    """Read a path from the first succeeded source, optionally projecting fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    source: str | list[str] = Field(alias="from")
    path: list[StrictStr | Annotated[StrictInt, Field(ge=0)]] = Field(default_factory=list)
    project: bool = False

    @property
    def sources(self) -> list[str]:
        """Return sources in their declared fallback order."""
        return self.source if isinstance(self.source, list) else [self.source]

    def resolve(
        self,
        run_input: Any,
        outputs: Mapping[str, Any],
        *,
        target_schema: Mapping[str, Any] | None = None,
    ) -> Any:
        """Return a source value, or ABSENT for a unsuccessful source/missing path."""
        value = next(
            (
                run_input if source == INPUT_SOURCE else outputs[source]
                for source in self.sources
                if source == INPUT_SOURCE or source in outputs
            ),
            ABSENT,
        )
        for part in self.path:
            if isinstance(value, dict) and isinstance(part, str):
                value = value.get(part, ABSENT)
            elif isinstance(value, list) and isinstance(part, int) and part < len(value):
                value = value[part]
            else:
                return ABSENT
        if self.project and isinstance(value, dict) and target_schema is not None:
            shape = schema_at(dict(target_schema), []) or {}
            value = {name: item for name, item in value.items() if name in shape.get("properties", {})}
        return copy.deepcopy(value) if value is not ABSENT else ABSENT


class ValueBinding(BaseModel):
    """Supply one literal JSON value."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: JsonValue

    def resolve(self, run_input: Any, outputs: Mapping[str, Any]) -> Any:
        """Return an independent copy of the authored value."""
        return copy.deepcopy(self.value)


type Binding = SourceBinding | ValueBinding
type InputBinding = SourceBinding | dict[str, Binding]


def resolve_bindings(
    binding: InputBinding,
    run_input: Any,
    outputs: Mapping[str, Any],
    *,
    target_schema: Mapping[str, Any] | None = None,
) -> Any:
    """Resolve a whole input or a field mapping, omitting absent fields."""
    if isinstance(binding, SourceBinding):
        return binding.resolve(run_input, outputs, target_schema=target_schema)
    resolved = {key: value.resolve(run_input, outputs) for key, value in binding.items()}
    return {key: value for key, value in resolved.items() if value is not ABSENT}
