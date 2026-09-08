"""Typed, transport-free workflow input bindings and evaluation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeAlias

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_slug
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from angee.workflows.attempts import JsonPresence

PathSegment: TypeAlias = StrictStr | Annotated[StrictInt, Field(ge=0)]
BindingPath: TypeAlias = tuple[str | int, ...]


class _Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        raise NotImplementedError

    def visits(self, binding_path: BindingPath = (), target_path: BindingPath = ()) -> tuple["BindingVisit", ...]:
        """Return this binding tree with exact persisted and target locations."""

        return (BindingVisit(self, binding_path, target_path),)

    def source_reference(self) -> "SourceReference | None":
        return None


class ConstantBinding(_Binding):
    kind: Literal["constant"]
    value: JsonValue

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        del context, path
        return BindingEvaluation(JsonPresence(True, copy.deepcopy(self.value)), {"kind": "constant"}, ())


class WorkflowInputBinding(_Binding):
    kind: Literal["workflow_input"]
    path: list[PathSegment] = Field(default_factory=list)

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        return context.workflow_input.evaluate(self.path, path)

    def source_reference(self) -> "SourceReference":
        return SourceReference("workflow_input", tuple(self.path))


class StepOutputBinding(_Binding):
    kind: Literal["step_output"]
    step_key: str
    path: list[PathSegment] = Field(default_factory=list)

    @field_validator("step_key")
    @classmethod
    def step_key_uses_model_slug_grammar(cls, value: str) -> str:
        try:
            validate_slug(value)
        except DjangoValidationError as error:
            raise ValueError("Step key must be a valid slug.") from error
        return value

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        source = context.step_outputs.get(self.step_key)
        if source is None:
            source = UnavailableSource(
                "source_missing",
                f"Step output {self.step_key!r} is unavailable.",
                {"kind": "step_output", "step_key": self.step_key},
            )
        return source.evaluate(self.path, path)

    def source_reference(self) -> "SourceReference":
        return SourceReference("step_output", tuple(self.path), self.step_key)


class MapItemBinding(_Binding):
    kind: Literal["map_item"]
    path: list[PathSegment] = Field(default_factory=list)

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        return context.map_item.evaluate(self.path, path)

    def source_reference(self) -> "SourceReference":
        return SourceReference("map_item", tuple(self.path))


class ObjectBinding(_Binding):
    kind: Literal["object"]
    fields: dict[str, "BindingNode"]

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        values: dict[str, JsonValue] = {}
        provenance: dict[str, JsonValue] = {}
        diagnostics: list[BindingDiagnostic] = []
        for key in sorted(self.fields):
            result = self.fields[key].evaluate(context, (*path, "fields", key))
            diagnostics.extend(result.diagnostics)
            if result.value is not None:
                values[key] = result.value.value
            if result.provenance is not None:
                provenance[key] = result.provenance
        if diagnostics:
            return BindingEvaluation(None, None, tuple(diagnostics))
        return BindingEvaluation(JsonPresence(True, values), {"kind": "object", "fields": provenance}, ())

    def visits(self, binding_path: BindingPath = (), target_path: BindingPath = ()) -> tuple["BindingVisit", ...]:
        children = tuple(
            visit
            for key, child in self.fields.items()
            for visit in child.visits((*binding_path, "fields", key), (*target_path, key))
        )
        return (BindingVisit(self, binding_path, target_path), *children)


class ArrayBinding(_Binding):
    kind: Literal["array"]
    items: list["BindingNode"]

    def evaluate(self, context: "BindingContext", path: BindingPath) -> "BindingEvaluation":
        values: list[JsonValue] = []
        provenance: list[JsonValue] = []
        diagnostics: list[BindingDiagnostic] = []
        for index, child in enumerate(self.items):
            result = child.evaluate(context, (*path, "items", index))
            diagnostics.extend(result.diagnostics)
            if result.value is not None:
                values.append(result.value.value)
            if result.provenance is not None:
                provenance.append(result.provenance)
        if diagnostics:
            return BindingEvaluation(None, None, tuple(diagnostics))
        return BindingEvaluation(JsonPresence(True, values), {"kind": "array", "items": provenance}, ())

    def visits(self, binding_path: BindingPath = (), target_path: BindingPath = ()) -> tuple["BindingVisit", ...]:
        children = tuple(
            visit
            for index, child in enumerate(self.items)
            for visit in child.visits((*binding_path, "items", index), (*target_path, index))
        )
        return (BindingVisit(self, binding_path, target_path), *children)


BindingNode: TypeAlias = Annotated[
    ConstantBinding | WorkflowInputBinding | StepOutputBinding | MapItemBinding | ObjectBinding | ArrayBinding,
    Field(discriminator="kind"),
]
ObjectBinding.model_rebuild(_types_namespace={"BindingNode": BindingNode})
ArrayBinding.model_rebuild(_types_namespace={"BindingNode": BindingNode})
_binding_adapter: TypeAdapter[BindingNode] = TypeAdapter(BindingNode)


@dataclass(frozen=True, slots=True)
class SourceReference:
    kind: Literal["workflow_input", "step_output", "map_item"]
    path: BindingPath
    step_key: str | None = None


@dataclass(frozen=True, slots=True)
class BindingVisit:
    binding: Any
    binding_path: BindingPath
    target_path: BindingPath


@dataclass(frozen=True, slots=True)
class SourceValue:
    """A source value and identity already authorized by runtime assembly."""

    value: JsonPresence
    provenance: dict[str, JsonValue]

    def evaluate(self, source_path: list[PathSegment], path: BindingPath) -> "BindingEvaluation":
        provenance = copy.deepcopy(self.provenance)
        provenance["path"] = list(source_path)
        if not self.value.present:
            return BindingEvaluation(
                None,
                None,
                (
                    BindingDiagnostic(
                        "source_absent", path, "The referenced value is absent.", provenance, tuple(source_path)
                    ),
                ),
            )
        current: JsonValue = self.value.value
        traversed: list[str | int] = []
        for segment in source_path:
            traversed.append(segment)
            if isinstance(segment, str) and isinstance(current, dict):
                if segment not in current:
                    return _path_error("path_missing", path, provenance, traversed)
                current = current[segment]
            elif isinstance(segment, int) and isinstance(current, list):
                if segment >= len(current):
                    return _path_error("path_missing", path, provenance, traversed)
                current = current[segment]
            else:
                return _path_error("path_type", path, provenance, traversed)
        return BindingEvaluation(JsonPresence(True, copy.deepcopy(current)), provenance, ())


@dataclass(frozen=True, slots=True)
class UnavailableSource:
    """A source rejected by its lifecycle or graph owner."""

    code: str
    message: str
    provenance: dict[str, JsonValue]

    def evaluate(self, source_path: list[PathSegment], path: BindingPath) -> "BindingEvaluation":
        provenance = copy.deepcopy(self.provenance)
        provenance["path"] = list(source_path)
        return BindingEvaluation(
            None,
            None,
            (BindingDiagnostic(self.code, path, self.message, provenance, tuple(source_path)),),
        )


BindingSource: TypeAlias = SourceValue | UnavailableSource


@dataclass(frozen=True, slots=True)
class BindingContext:
    workflow_input: BindingSource
    step_outputs: dict[str, BindingSource]
    map_item: BindingSource


@dataclass(frozen=True, slots=True)
class BindingDiagnostic:
    code: str
    path: BindingPath
    message: str
    source: dict[str, JsonValue] | None = None
    source_path: BindingPath = ()


@dataclass(frozen=True, slots=True)
class BindingEvaluation:
    value: JsonPresence | None
    provenance: dict[str, JsonValue] | None
    diagnostics: tuple[BindingDiagnostic, ...]


def parse_binding(value: JsonValue) -> BindingNode:
    """Parse the single persisted binding grammar through Pydantic."""

    return _binding_adapter.validate_python(value)


def binding_error_locations(value: JsonValue, error: PydanticValidationError) -> tuple[BindingPath, ...]:
    """Remove Pydantic union branch tags while preserving exact stored binding keys."""

    return tuple(_binding_error_location(value, tuple(item["loc"])) for item in error.errors(include_url=False))


def binding_error_details(
    value: JsonValue,
    error: PydanticValidationError,
) -> tuple[tuple[BindingPath, str], ...]:
    """Project native parser errors into stable authoring paths and messages."""

    return tuple(
        (
            _binding_error_location(value, tuple(item["loc"])),
            _binding_error_message(item),
        )
        for item in error.errors(include_url=False)
    )


def _binding_error_message(item: Mapping[str, Any]) -> str:
    if item.get("type") in {"union_tag_not_found", "union_tag_invalid"}:
        return "Choose a value type."
    return str(item["msg"])


_BINDING_KINDS = frozenset({"constant", "workflow_input", "step_output", "map_item", "object", "array"})


def _binding_error_location(value: Any, location: tuple[Any, ...]) -> BindingPath:
    if not isinstance(value, dict):
        return ()
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _BINDING_KINDS:
        return ()
    remaining = location[1:] if location and location[0] == kind else location
    if not remaining:
        return ()
    field = remaining[0]
    if not isinstance(field, (str, int)):
        field = str(field)
    stored: BindingPath = (field,)
    remaining = remaining[1:]
    if kind == "object" and field == "fields" and remaining:
        key = remaining[0]
        if not isinstance(key, str):
            key = str(key)
        child = value.get("fields", {}).get(key) if isinstance(value.get("fields"), dict) else None
        return (*stored, key, *_binding_error_location(child, remaining[1:]))
    if kind == "array" and field == "items" and remaining:
        index = remaining[0]
        items = value.get("items")
        child = items[index] if isinstance(index, int) and isinstance(items, list) and 0 <= index < len(items) else None
        return (
            *stored,
            index if isinstance(index, int) else str(index),
            *_binding_error_location(child, remaining[1:]),
        )
    current = value.get(field) if isinstance(field, str) else None
    for part in remaining:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            stored = (*stored, part)
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            stored = (*stored, part)
            current = current[part]
        else:
            break
    return stored


def evaluate_binding(binding: BindingNode, context: BindingContext) -> BindingEvaluation:
    """Resolve a parsed binding from supplied snapshots without database work."""

    return binding.evaluate(context, ())


def _path_error(
    code: str, path: BindingPath, provenance: dict[str, JsonValue], traversed: list[str | int]
) -> BindingEvaluation:
    message = (
        "The referenced path does not exist."
        if code == "path_missing"
        else "The referenced path crosses an incompatible value."
    )
    return BindingEvaluation(None, None, (BindingDiagnostic(code, path, message, provenance, tuple(traversed)),))
