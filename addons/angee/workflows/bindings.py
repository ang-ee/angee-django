"""Pure input/result bindings and conservative schema compatibility checks."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr

from angee.base.jsonschema import LocalSchemaReferences
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
            value = {name: item for name, item in value.items() if name in target_schema.get("properties", {})}
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


def schema_at(
    schema: dict[str, Any], path: list[str | int], *, required: bool = False
) -> dict[str, Any] | None:
    """Follow a Pydantic object/list path, optionally proving each part exists.

    A composed ``allOf`` constraint may prove a required path independently;
    unproven paths are rejected without attempting general schema implication.
    """
    references = LocalSchemaReferences(schema)
    current: Any = schema
    for index in range(len(path) + 1):
        visited: set[str] = set()
        while "$ref" in current:
            reference = current["$ref"]
            if reference in visited:
                return None
            visited.add(reference)
            current = references.resolve(reference)
            if not isinstance(current, dict):
                return None
        if index == len(path):
            return {**current, "$defs": schema.get("$defs", {})}
        part = path[index]
        if required and not (
            part in current.get("required", [])
            if isinstance(part, str)
            else part < current.get("minItems", 0)
        ):
            for constraint in current.get("allOf", []):
                actual = schema_at(
                    {**constraint, "$defs": schema.get("$defs", {})}, path[index:], required=True
                )
                if actual is not None:
                    return actual
            return None
        if isinstance(part, str):
            current = current.get("properties", {}).get(part)
        else:
            positional = current.get("prefixItems", [])
            current = positional[part] if part < len(positional) else current.get("items")
        if not isinstance(current, dict):
            return None
    return None


def schemas_match(source: dict[str, Any], target: dict[str, Any]) -> bool:
    """Prove equality of contracts, ignoring presentation/default annotations.

    This deliberately makes no claim to general JSON Schema entailment. More
    permissive assignments need an explicit projection or matching declarations.
    """
    ignored = {"title", "description", "default", "examples", "$defs"}

    def normalize(value: Any, references: LocalSchemaReferences, active: frozenset[str]) -> Any:
        if isinstance(value, list):
            return [normalize(item, references, active) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if reference in active:
                return {"$ref": reference}
            value = references.resolve(reference)
            if value is None:
                return {"$ref": reference}
            return normalize(value, references, active | {reference})
        return {key: normalize(item, references, active) for key, item in value.items() if key not in ignored}

    return normalize(source, LocalSchemaReferences(source), frozenset()) == normalize(
        target,
        LocalSchemaReferences(target),
        frozenset(),
    )
