"""The graph document, its validation, and database-free execution planning."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal, Protocol

from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from angee.base.jsonschema import embed_schema, schema_at, schemas_match, unmatched_properties, validate, validator
from angee.base.serialization import canonical_json
from angee.workflows.bindings import (
    ABSENT,
    InputBinding,
    SourceBinding,
    ValueBinding,
    resolve_bindings,
)
from angee.workflows.states import ERROR_OUTCOME, INPUT_SOURCE, ITEM_SOURCE, NodeKey, Outcome, StepRunStatus
from angee.workflows.steps import Step, resolve_step


class Issue(BaseModel):
    """One document issue located in the authored document."""

    node: str | None = None
    path: list[str | int] = Field(default_factory=list)
    code: str
    message: str

    @property
    def blocks_draft(self) -> bool:
        """Whether this issue rejects a draft as well as a publication."""
        return self.code in {"parse", "unknown_step"}


class DefinitionInvalid(ValidationError):
    """A document cannot be published; typed diagnostics remain available."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__([issue.message for issue in issues])


class Node(BaseModel):
    """One registered step and its data/control declarations."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    step: str
    config: dict[str, Any] = Field(default_factory=dict)
    input: InputBinding | None = None
    join: Literal["any", "all"] = "any"
    next: dict[Outcome, str | list[str]] = Field(default_factory=dict)

    def targets(self, outcome: str) -> list[str]:
        """Return targets of one outcome, preserving authored fan-out order."""
        targets = self.next.get(outcome, [])
        return targets if isinstance(targets, list) else [targets]


class ResultBinding(BaseModel):
    """A producer eligible to supply the terminal run result."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    source: str = Field(alias="from")
    when: list[Outcome] | None = None
    outcome: Outcome | None = Field(default=None, alias="as")
    output: InputBinding | None = None

    def eligible(self, outcome: str) -> bool:
        """Require an explicit selection to report a producer's failure."""
        return outcome != ERROR_OUTCOME if self.when is None else outcome in self.when


class StepRow(Protocol):
    """The minimal persisted facts consulted by graph planning."""

    node_key: str
    map_index: int
    status: str
    outcome: str


@dataclass(frozen=True)
class PlannedNode:
    """A node whose row should be inserted by the run owner."""

    node_key: str
    status: Literal["ready", "skipped"]
    rank: int


class Definition(BaseModel):
    """The sole declaration shape and graph policy for a workflow document."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    nodes: dict[NodeKey, Node] = Field(default_factory=dict)
    results: list[ResultBinding] = Field(default_factory=list)

    @classmethod
    def check(cls, document: Any, *, subject_model: str | None = None) -> tuple[Definition | None, list[Issue]]:
        """Parse a document and return the complete typed validation issue list."""
        try:
            definition = cls.model_validate(document)
        except PydanticValidationError as error:
            return None, [
                Issue(
                    node=str(item["loc"][1]) if len(item["loc"]) > 1 and item["loc"][0] == "nodes" else None,
                    path=list(item["loc"]),
                    code="parse",
                    message=item["msg"],
                )
                for item in error.errors(include_url=False, include_context=False, include_input=False)
            ]
        return definition, definition.issues(subject_model=subject_model)

    def node(self, key: str) -> Node:
        """Return the declaration for one stable node key."""
        return self.nodes[key]

    @cached_property
    def _steps(self) -> dict[str, type[Step[Any, Any, Any]]]:
        """Retain only implementation classes already requested from this graph."""
        return {}

    def step(self, key: str) -> type[Step[Any, Any, Any]]:
        """Resolve one node's class once, without resolving unrelated draft nodes."""
        if key not in self._steps:
            self._steps[key] = resolve_step(self.node(key).step)
        return self._steps[key]

    @cached_property
    def predecessors(self) -> dict[str, dict[str, set[str]]]:
        """Map each target to source nodes and the outcomes that connect them."""
        incoming: dict[str, dict[str, set[str]]] = {key: {} for key in self.nodes}
        for source, node in self.nodes.items():
            for outcome in node.next:
                for target in node.targets(outcome):
                    if target in incoming:
                        incoming[target].setdefault(source, set()).add(outcome)
        return incoming

    @cached_property
    def graph(self) -> dict[str, set[str]]:
        """Return the dependency graph shared by validation and execution."""
        return {key: set(self.predecessors[key]) for key in sorted(self.nodes)}

    @cached_property
    def ranks(self) -> dict[str, int]:
        """Stable execution ranks, with dependency order owned by graphlib."""
        graph = {key: sorted(sources) for key, sources in self.graph.items()}
        return {key: rank for rank, key in enumerate(TopologicalSorter(graph).static_order())}

    @cached_property
    def entries(self) -> list[str]:
        """Return nodes with no predecessor, including incomplete drafts."""
        return [key for key, sources in self.graph.items() if not sources]

    @property
    def entry(self) -> str:
        """Derive the unique entry; incomplete drafts have no usable entry."""
        if len(self.entries) != 1:
            raise ValidationError("A workflow requires exactly one entry node.")
        return self.entries[0]

    def issues(self, *, subject_model: str | None = None) -> list[Issue]:
        """Return publication issues; only parse/unknown-step issues block drafts."""
        issues, offered = self._node_issues(subject_model)
        issues.extend(self._graph_issues())
        issues.extend(self._input_issues())
        issues.extend(self._result_issues(offered))
        if not issues:
            issues.extend(self._derived_contract_issues())
        return issues

    def _node_issues(self, subject_model: str | None) -> tuple[list[Issue], dict[str, dict[str, str]]]:
        """Check registered steps, their declarations, and authored outgoing edges."""
        issues: list[Issue] = []
        offered: dict[str, dict[str, str]] = {}
        for key, node in self.nodes.items():
            if key in {INPUT_SOURCE, ITEM_SOURCE}:
                issues.append(
                    Issue(
                        node=key,
                        path=["nodes", key],
                        code="reserved_node",
                        message="This key is reserved for a binding source.",
                    )
                )
            try:
                step = self.step(key)
            except ImproperlyConfigured as error:
                issues.append(Issue(node=key, path=["nodes", key, "step"], code="unknown_step", message=str(error)))
                continue
            required_subject = step.subject
            if (
                subject_model is not None
                and required_subject is not None
                and subject_model != required_subject
            ):
                issues.append(
                    Issue(
                        node=key,
                        path=["nodes", key, "step"],
                        code="subject",
                        message=f"Step requires subject model {required_subject!r}.",
                    )
                )
            for name, schema in (("input", step.input_schema), ("output", step.output_schema)):
                try:
                    schema()
                except ValidationError as error:
                    issues.append(
                        Issue(node=key, path=["nodes", key, "step"], code="schema", message=f"{name}: {error}")
                    )
            try:
                config = step.parse_config(node.config)
            except ValidationError as error:
                issues.append(Issue(node=key, path=["nodes", key, "config"], code="config", message=str(error)))
                continue
            try:
                outcomes = step.available_outcomes(config, validate=True)
            except ValidationError as error:
                issues.append(Issue(node=key, path=["nodes", key, "step"], code="outcome", message=str(error)))
                continue
            offered[key] = outcomes
            for outcome in sorted(step.required_outcomes(config)):
                if not node.targets(outcome):
                    issues.append(Issue(
                        node=key, path=["nodes", key, "next", outcome], code="required_outcome",
                        message=f"This step requires a route for outcome {outcome!r}.",
                    ))
            for outcome in node.next:
                if not node.targets(outcome):
                    issues.append(
                        Issue(
                            node=key,
                            path=["nodes", key, "next", outcome],
                            code="target",
                            message="An outcome entry requires at least one target; omit it to end the branch.",
                        )
                    )
                if outcome not in outcomes:
                    issues.append(
                        Issue(
                            node=key,
                            path=["nodes", key, "next", outcome],
                            code="outcome",
                            message=f"Unknown outcome {outcome!r}.",
                        )
                    )
                for target in node.targets(outcome):
                    if target not in self.nodes:
                        issues.append(
                            Issue(
                                node=key,
                                path=["nodes", key, "next", outcome],
                                code="target",
                                message=f"Unknown target {target!r}.",
                            )
                        )
        return issues, offered

    def _graph_issues(self) -> list[Issue]:
        """Check acyclicity, the single entry, and reachability in stable order."""
        issues: list[Issue] = []
        try:
            _ = self.ranks
        except CycleError as error:
            issues.append(Issue(code="cycle", message=str(error)))
        if len(self.entries) != 1:
            issues.append(Issue(code="entry", message="A workflow requires exactly one entry node."))
        if len(self.entries) == 1:
            outgoing: dict[str, set[str]] = {key: set() for key in self.nodes}
            for target, sources in self.graph.items():
                for source in sources:
                    outgoing[source].add(target)
            reached = self._walk(outgoing, self.entries)
            issues.extend(
                Issue(node=key, path=["nodes", key], code="unreachable", message="Node is unreachable.")
                for key in self.nodes
                if key not in reached
            )
        return issues

    def _input_issues(self) -> list[Issue]:
        """Check explicit bindings and the default input supplied by incoming edges."""
        issues: list[Issue] = []
        incoming = self.predecessors
        for key, node in self.nodes.items():
            try:
                target_schema = self.step(key).input_schema()
            except (ImproperlyConfigured, ValidationError):
                continue
            if node.input is not None:
                issues.extend(
                    self._binding_issues(
                        node.input,
                        target_schema,
                        key,
                        ["nodes", key, "input"],
                        allowed=self.ancestors(key) | {INPUT_SOURCE},
                    )
                )
            elif len(incoming[key]) > 1:
                issues.append(
                    Issue(
                        node=key,
                        path=["nodes", key, "input"],
                        code="input",
                        message="Multiple predecessor nodes require explicit input bindings.",
                    )
                )
            else:
                for source_key, routed_outcomes in incoming[key].items():
                    try:
                        source_step = self.step(source_key)
                        source_schema = source_step.output_schema()
                    except (ImproperlyConfigured, ValidationError):
                        continue
                    if (
                        routed_outcomes - source_step.empty_outcomes
                        and target_schema
                        and not schemas_match(source_schema, target_schema)
                    ):
                        issues.append(
                            Issue(
                                node=key,
                                path=["nodes", key, "input"],
                                code="input",
                                message=f"Incompatible default input from {source_key!r}; bind explicitly.",
                            )
                        )
                    if routed_outcomes & source_step.empty_outcomes and not self._validator(target_schema).is_valid({}):
                        issues.append(
                            Issue(
                                node=key,
                                path=["nodes", key, "input"],
                                code="input",
                                message=("An empty-output edge, including an error edge, supplies {}; "
                                         "required input fields need explicit bindings."),
                            )
                        )
        return issues

    def _result_issues(self, offered: Mapping[str, dict[str, str]]) -> list[Issue]:
        """Check producer outcomes and the permitted sources of terminal result bindings."""
        issues: list[Issue] = []
        for index, result in enumerate(self.results):
            path: list[str | int] = ["results", index]
            if result.source not in self.nodes:
                issues.append(Issue(path=path, code="result", message=f"Unknown result producer {result.source!r}."))
            elif result.source in offered:
                outcomes = offered[result.source]
                if result.when is not None and set(result.when) - outcomes.keys():
                    issues.append(Issue(path=path, code="result", message="Result names an unknown producer outcome."))
                if result.output is not None:
                    if isinstance(result.output, SourceBinding) and any(
                        result.eligible(outcome) for outcome in self.step(result.source).empty_outcomes
                    ):
                        issues.append(
                            Issue(
                                path=[*path, "output"],
                                code="binding",
                                message=("A whole result binding cannot select empty output, "
                                         "including the error outcome."),
                            )
                        )
                    allowed = (
                        {result.source, INPUT_SOURCE}
                        if isinstance(result.output, SourceBinding)
                        else ({result.source} | self.ancestors(result.source))
                    )
                    issues.extend(
                        self._binding_issues(
                            result.output,
                            {},
                            None,
                            [*path, "output"],
                            allowed=allowed,
                        )
                    )
        return issues

    def _derived_contract_issues(self) -> list[Issue]:
        """Check composed admission schemas and required whole-result paths last."""
        issues: list[Issue] = []
        try:
            self.input_schema
            for index, result in enumerate(self.results):
                self.result_schema(result)
                if isinstance(result.output, SourceBinding):
                    for source in result.output.sources:
                        source_schema = self._source_schema(source)
                        if schema_at(source_schema, result.output.path, required=True) is None:
                            issues.append(
                                Issue(
                                    path=["results", index, "output"],
                                    code="binding",
                                    message="A whole result binding path must be required at every level.",
                                )
                            )
        except ValidationError as error:
            issues.append(Issue(code="binding", message=str(error)))
        return issues

    def _binding_issues(
        self,
        binding: InputBinding,
        target: dict[str, Any],
        node: str | None,
        path: list[str | int],
        *,
        allowed: set[str],
    ) -> list[Issue]:
        issues: list[Issue] = []
        target = schema_at(target, []) or {}
        fields = self._bindings(binding)
        if not isinstance(binding, SourceBinding):
            for required in set(target.get("required", [])) - binding.keys():
                issues.append(
                    Issue(
                        node=node,
                        path=[*path, required],
                        code="binding",
                        message="A required input field has no binding.",
                    )
                )
        for field, value in fields.items():
            location = path if field is None else [*path, field]
            expected = self._field_schema(target, field)
            if expected is None:
                issues.append(Issue(node=node, path=location, code="binding", message="Unknown target input field."))
                continue
            if isinstance(value, ValueBinding):
                for error in self._validator(expected).iter_errors(value.value):
                    issues.append(Issue(
                        node=node, path=location, code="binding", message=f"{error.json_path}: {error.message}",
                    ))
                continue
            if not value.sources:
                issues.append(Issue(node=node, path=location, code="binding", message="A source list cannot be empty."))
            if value.project and (field is not None or not target):
                issues.append(
                    Issue(
                        node=node,
                        path=location,
                        code="binding",
                        message="Projection requires a whole-object input binding and target schema.",
                    )
                )
            for source in value.sources:
                if source not in allowed:
                    issues.append(
                        Issue(
                            node=node,
                            path=location,
                            code="binding",
                            message="A binding source must be an allowed producer or control-flow ancestor.",
                        )
                    )
                if source == INPUT_SOURCE:
                    continue
                try:
                    schema = self.step(source).output_schema()
                except (KeyError, ImproperlyConfigured):
                    issues.append(
                        Issue(node=node, path=location, code="binding", message=f"Unknown source {source!r}.")
                    )
                    continue
                except ValidationError:
                    # The source node already reports its invalid schema declaration.
                    continue
                actual = schema_at(schema, value.path)
                if actual is None:
                    issues.append(
                        Issue(node=node, path=location, code="binding", message="Unknown source output path.")
                    )
                elif expected and value.project:
                    for name in unmatched_properties(actual, expected):
                        issues.append(
                            Issue(
                                node=node,
                                path=location,
                                code="binding",
                                message=f"Projection cannot supply field {name!r}.",
                            )
                        )
                elif expected and not schemas_match(actual, expected):
                    issues.append(
                        Issue(
                            node=node,
                            path=location,
                            code="binding",
                            message="Source and target schemas differ; compatibility is unproven.",
                        )
                    )
        return issues

    @cached_property
    def _validators(self) -> dict[str, Any]:
        return {}

    def _validator(self, schema: dict[str, Any]) -> Any:
        """Retain each checked schema validator for this immutable definition."""
        key = canonical_json(schema)
        if key not in self._validators:
            self._validators[key] = validator(schema)
        return self._validators[key]

    def _source_schema(self, source: str) -> dict[str, Any]:
        return self.input_schema if source == INPUT_SOURCE else self.step(source).output_schema()

    @staticmethod
    def _field_schema(target: dict[str, Any], field: str | None) -> dict[str, Any] | None:
        """Whole and untyped bindings retain their contract; typed fields project it."""
        return target if field is None or not target else schema_at(target, [field])

    @staticmethod
    def _bindings(binding: InputBinding | None) -> Mapping[str | None, SourceBinding | ValueBinding]:
        if isinstance(binding, SourceBinding):
            return {None: binding}
        return {field: value for field, value in (binding or {}).items()}

    @staticmethod
    def _walk(graph: Mapping[str, set[str]], starts: Iterable[str]) -> set[str]:
        pending = list(starts)
        reached: set[str] = set()
        while pending:
            key = pending.pop()
            if key not in reached:
                reached.add(key)
                pending.extend(graph[key])
        return reached

    def ancestors(self, key: str) -> set[str]:
        """Return control ancestors through the shared cycle-tolerant graph walk."""
        return self._walk(self.graph, self.graph[key])

    def unrouted_failure(self, rows: Iterable[StepRow]) -> bool:
        """Whether a failed node has no declared route for its built-in failure."""
        return any(
            row.status == StepRunStatus.FAILED and self.retry_allowed(row.node_key)
            for row in rows
        )

    def retry_allowed(self, key: str) -> bool:
        """In-place recovery must not replay a failure already routed through error."""
        return ERROR_OUTCOME not in self.nodes[key].next

    def ready_nodes(self, rows: Iterable[StepRow]) -> list[PlannedNode]:
        """Plan missing rows once all sources settle, propagating skips in one pass.

        Incoming edges are grouped by source: two alternative outcomes leading
        to the same target count as one predecessor for an ``all`` join.
        Failed ``error`` edges are live, matching the failure-routing contract.
        """
        existing = {row.node_key: row for row in rows if row.map_index == 0}
        incoming = self.predecessors
        planned: list[PlannedNode] = []
        skipped: set[str] = set()
        for key, rank in self.ranks.items():
            if key in existing:
                continue
            sources = incoming[key]
            if not sources:
                planned.append(PlannedNode(key, "ready", rank))
                continue
            if any(
                source not in skipped
                and (source not in existing or existing[source].status not in StepRunStatus.terminal_values())
                for source in sources
            ):
                continue
            live = [
                source not in skipped and self._edge_live(existing[source], outcomes)
                for source, outcomes in sources.items()
            ]
            active = all(live) if self.nodes[key].join == "all" else any(live)
            status: Literal["ready", "skipped"] = "ready" if active else "skipped"
            planned.append(PlannedNode(key, status, rank))
            if not active:
                skipped.add(key)
        return planned

    @staticmethod
    def _edge_live(row: StepRow, outcomes: set[str]) -> bool:
        return StepRunStatus(row.status).has_outcome and row.outcome in outcomes

    def input_for(self, key: str, run_input: Any, rows: Iterable[Any]) -> Any:
        """Bind raw node input; the attempt persists it before typed validation."""
        rows = list(rows)
        node = self.nodes[key]
        step = self.step(key)
        outputs = self._outputs(rows)
        if node.input is not None:
            value = resolve_bindings(
                node.input,
                run_input,
                outputs,
                target_schema=step.input_schema(),
            )
        elif key == self.entry:
            value = self._entry_input(run_input, step)
        else:
            source = next(iter(self.predecessors[key]))
            source_row = next(row for row in rows if row.node_key == source)
            value = source_row.output
        if value is ABSENT:
            raise ValidationError("The bound input source is absent.")
        if value is None:
            raise ValidationError("A whole step input cannot be null.")
        return value

    def _input_bindings(self) -> Iterator[tuple[SourceBinding, dict[str, Any], bool]]:
        for key, node in self.nodes.items():
            for field, binding in self._bindings(node.input).items():
                if isinstance(binding, SourceBinding) and INPUT_SOURCE in binding.sources:
                    target = schema_at(self.step(key).input_schema(), []) or {}
                    expected = self._field_schema(target, field)
                    if expected is None:
                        raise ValidationError("Unknown target input field.")
                    yield binding, expected, field is None or field in target.get("required", [])

    @cached_property
    def input_schema(self) -> dict[str, Any]:
        """Derive the admitted input from the entry and explicit input consumers.

        Binding paths contribute object properties or positional array items.
        Multiple consumers of the same path must declare equal schemas; broader
        schema implication is deliberately outside the binding contract.
        """
        entry = self.nodes[self.entry]
        schema = schema_at(self.step(self.entry).input_schema(), []) or {} if entry.input is None else {}
        definitions = schema.setdefault("$defs", {})
        seen: dict[tuple[str | int, ...], dict[str, Any]] = {}
        for binding, expected, required in self._input_bindings():
            path = tuple(binding.path)
            known = seen.get(path) or schema_at(schema, path)
            if known and expected and not schemas_match(known, expected) and not binding.project:
                raise ValidationError("Workflow input consumers declare incompatible schemas for the same path.")
            seen[path] = expected or known or {}
            expected = copy.deepcopy(expected)
            if binding.project:
                expected.pop("additionalProperties", None)
            embed_schema(expected, definitions, path=binding.path, required=required, into=schema)
        if not definitions:
            schema.pop("$defs")
        return schema

    def validate_input(self, value: Any) -> Any:
        """Normalize the entry and validate the derived workflow-input schema."""
        step = self.step(self.entry)
        normalized = step.normalize_input(self.input_for(self.entry, value, []))
        if self.nodes[self.entry].input is None:
            value = {**value, **normalized} if isinstance(value, dict) and isinstance(normalized, dict) else normalized
        validate(self._validator(self.input_schema), value)
        return value

    def _entry_input(self, value: Any, step: type[Step[Any, Any, Any]]) -> Any:
        if not isinstance(value, dict):
            return value
        if step.input_model is None:
            return value
        properties = (schema_at(step.input_schema(), []) or {}).get("properties", {})
        additional: set[str | int] = set()
        for binding, expected, _required in self._input_bindings():
            additional.update(binding.path[:1] or (schema_at(expected, []) or {}).get("properties", {}))
        return {key: item for key, item in value.items() if key not in additional or key in properties}

    def result_schema(self, result: ResultBinding) -> dict[str, Any]:
        """Describe one result's actual projection, retaining local schema refs."""
        if result.output is None:
            step = self.step(result.source)
            if any(result.eligible(outcome) for outcome in step.empty_outcomes):
                empty: dict[str, Any] = {"const": {}}
                return {"anyOf": [step.output_schema(), empty]} if (
                    result.when is None or set(result.when) - step.empty_outcomes
                ) else empty
            return step.output_schema()
        definitions: dict[str, Any] = {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field, binding in self._bindings(result.output).items():
            if isinstance(binding, ValueBinding):
                actual: dict[str, Any] = {"const": binding.value}
                if field is not None:
                    required.append(field)
            else:
                choices = []
                for source in binding.sources:
                    source_schema = self._source_schema(source)
                    source_value = schema_at(source_schema, binding.path)
                    if source_value is None:
                        raise ValidationError("Unknown result output path.")
                    choices.append(embed_schema(source_value, definitions))
                actual = choices[0] if len(choices) == 1 else {"anyOf": choices}
            if field is None:
                return {**actual, "$defs": definitions}
            properties[field] = actual
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
            "$defs": definitions,
        }

    def result_for(self, rows: Iterable[Any], run_input: Any) -> tuple[str, Any] | None:
        """Select the first eligible result in authored document order."""
        existing = {row.node_key: row for row in rows}
        outputs = self._outputs(existing.values())
        for result in self.results:
            producer = existing.get(result.source)
            if producer is None or not StepRunStatus(producer.status).has_outcome:
                continue
            outcome = producer.outcome
            if not result.eligible(outcome):
                continue
            output = producer.output
            if result.output is not None:
                output = resolve_bindings(result.output, run_input, outputs)
                if output is ABSENT:
                    raise ValidationError("The bound result source is absent.")
            return result.outcome or outcome, output
        return None

    @staticmethod
    def _outputs(rows: Iterable[Any]) -> dict[str, Any]:
        return {row.node_key: row.output for row in rows if row.status == StepRunStatus.SUCCEEDED}
