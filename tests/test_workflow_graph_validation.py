"""Publication-readiness tests for the immutable workflow graph owner."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from angee.workflows.bindings import parse_binding
from angee.workflows.data_contracts import model_data_contract, schema_data_contract
from angee.workflows.graph import (
    GraphEdge,
    GraphIdentity,
    GraphNode,
    WorkflowGraph,
    _choice_sets_coapplicable,
    _result_binding_compatible,
    _tagged_one_of_choice,
)
from angee.workflows.steps import GateStep, MapStep, StepImpl, StepResult, WaitStep
from angee.workflows_agents.steps import AgentSessionStepImpl
from angee.workflows_parties.steps import DedupeExecuteStepImpl, DedupeGateStepImpl, DedupeScanStepImpl
from example.notes.steps import NotePublishStep, NoteValidateForPublicationStep


class UnimplementedStep(StepImpl):
    """Local non-executable declaration used to exercise readiness diagnostics."""


class LegacyOutcomeStep(StepImpl):
    """Concrete legacy operation whose outcome vocabulary is unknown."""

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.done(outcome="legacy")


class AliasedMapStep(MapStep):
    """A registry alias the engine's exact Map predicate will not expand."""

    key = "map_alias"


class DeclaredOutput(BaseModel):
    payload: dict[str, str]


class DeclaredInput(BaseModel):
    title: str


class SourceStep(LegacyOutcomeStep):
    key = "source_contract"
    output_model = DeclaredOutput


class TargetStep(LegacyOutcomeStep):
    key = "target_contract"
    input_model = DeclaredInput


class SubjectStep(LegacyOutcomeStep):
    key = "subject_contract"
    subject_declaration = "notes.note"


class ExactLiteralOutput(BaseModel):
    status: Literal["held", "stale"]


class SubsetLiteralOutput(BaseModel):
    status: Literal["held"]


class SupersetLiteralOutput(BaseModel):
    status: Literal["held", "rejected"]


class UnboundedStringOutput(BaseModel):
    status: str


class NonemptyStringOutput(BaseModel):
    status: str = Field(min_length=1)


class BoundedStringOutput(BaseModel):
    status: str = Field(min_length=2, max_length=10)


class NestedNonemptyStringOutput(BaseModel):
    result: NonemptyStringOutput


class MixedStringVariantOutput(BaseModel):
    result: NonemptyStringOutput | UnboundedStringOutput


class BooleanLiteralOutput(BaseModel):
    status: Literal[True]


class ReferencedLiteralValue(BaseModel):
    status: Literal["held"]


class ReferencedLiteralOutput(BaseModel):
    result: ReferencedLiteralValue


class ObjectOutput(BaseModel):
    status: dict[str, str]


class ArrayOutput(BaseModel):
    status: list[str]


class NonNegativeIntegerOutput(BaseModel):
    revision: int = Field(ge=0)


class PositiveIntegerOutput(BaseModel):
    revision: int = Field(gt=0)


class BoundedIntegerOutput(BaseModel):
    revision: int = Field(ge=0, le=10)


class UnderTenIntegerOutput(BaseModel):
    revision: int = Field(lt=10)


class UnboundedIntegerOutput(BaseModel):
    revision: int


class NegativeIntegerOutput(BaseModel):
    revision: int = Field(ge=-1)


class NestedNonNegativeIntegerOutput(BaseModel):
    result: NonNegativeIntegerOutput


class MixedNumericVariantOutput(BaseModel):
    result: NonNegativeIntegerOutput | UnboundedIntegerOutput


def node(
    key: str,
    impl: type[StepImpl],
    config: Any = None,
    *,
    entry: bool = False,
    binding: Any = None,
) -> GraphNode:
    return GraphNode(
        GraphIdentity(client_key=f"node-{key}"),
        GraphIdentity(client_key="workflow-1"),
        key,
        entry,
        impl.key,
        impl,
        {} if config is None else config,
        key.title(),
        binding,
    )


def edge(source: str, target: str, condition: str = "") -> GraphEdge:
    return GraphEdge(
        GraphIdentity(client_key=f"edge-{source}-{target}-{condition}"),
        GraphIdentity(client_key="workflow-1"),
        GraphIdentity(client_key=f"node-{source}"),
        GraphIdentity(client_key=f"node-{target}"),
        source,
        target,
        condition,
    )


def graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge] | None = None,
    *,
    max_steps: int = 100,
    subject_declaration: str = "",
    output_schema: dict[str, Any] | None = None,
    result_rules: list[dict[str, Any]] | None = None,
) -> WorkflowGraph:
    return WorkflowGraph(
        GraphIdentity(client_key="workflow-1"),
        max_steps,
        tuple(nodes),
        tuple(edges or []),
        subject_declaration,
        {"type": "object", "properties": {}},
        output_schema or {"type": "object", "properties": {}, "additionalProperties": False},
        result_rules or [],
    )


def codes(value: WorkflowGraph) -> set[str]:
    return {diagnostic.code for diagnostic in value.diagnostics()}


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
@pytest.mark.parametrize("reference", ["https://example.invalid/schema", "other.json"])
def test_graph_rejects_nonlocal_schema_references(field: str, reference: str) -> None:
    value = replace(graph([]), **{field: {"type": "object", "properties": {"value": {"$ref": reference}}}})
    assert f"{field}_invalid" in codes(value)


def test_graph_reports_unresolvable_input_references_for_root_bindings() -> None:
    value = replace(
        graph(
            [node("producer", LegacyOutcomeStep, entry=True)],
            result_rules=[{
                "outcome": "completed",
                "producer": "producer",
                "when_outcome": "",
                "binding": {"kind": "workflow_input", "path": []},
            }],
        ),
        input_schema={"$ref": "#/$defs/missing"},
    )
    assert "input_schema_invalid" in codes(value)


def test_result_exclusivity_proof_avoids_terminal_path_cartesian_product() -> None:
    branch = GraphIdentity(client_key="shared-branch")
    left_paths = tuple({branch: "left"} for _ in range(4_096))
    right_paths = tuple({branch: "right"} for _ in range(4_096))

    assert not _choice_sets_coapplicable(left_paths, right_paths)
    assert _choice_sets_coapplicable(left_paths, (*right_paths, {}))


@pytest.mark.parametrize(
    ("source_model", "expected"),
    [
        (ExactLiteralOutput, True),
        (SubsetLiteralOutput, True),
        (SupersetLiteralOutput, False),
        (UnboundedStringOutput, False),
    ],
)
def test_result_binding_proves_only_bounded_literal_subsets(
    source_model: type[BaseModel], expected: bool,
) -> None:
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["status"],
    })
    compatible = _result_binding_compatible(
        binding,
        {"type": "string", "enum": ["held", "stale"]},
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(source_model, mode="serialization"),
        "producer",
    )

    assert compatible is expected


class DefaultedCodeOutput(BaseModel):
    code: str = ""


def test_result_binding_ignores_schema_annotations() -> None:
    """A ``default`` annotates a field; it never blocks the field's proof."""

    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["code"],
    })

    assert _result_binding_compatible(
        binding,
        {"type": "string", "default": "", "title": "Code"},
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(DefaultedCodeOutput, mode="serialization"),
        "producer",
    )


def test_whole_output_reference_proves_an_identical_object_schema() -> None:
    schema = DefaultedCodeOutput.model_json_schema(mode="serialization")
    binding = parse_binding({"kind": "step_output", "step_key": "producer", "path": []})

    assert _result_binding_compatible(
        binding,
        schema,
        schema_data_contract({"type": "object", "properties": {}}),
        schema_data_contract(schema),
        "producer",
    )
    assert not _result_binding_compatible(
        binding,
        {**schema, "required": ["code"]},
        schema_data_contract({"type": "object", "properties": {}}),
        schema_data_contract(schema),
        "producer",
    )


def test_result_binding_literal_subset_uses_json_type_semantics() -> None:
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["status"],
    })

    assert not _result_binding_compatible(
        binding,
        {"type": ["boolean", "integer"], "enum": [1]},
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(BooleanLiteralOutput, mode="serialization"),
        "producer",
    )


@pytest.mark.parametrize(
    ("source_model", "target_schema", "expected"),
    [
        (NonemptyStringOutput, {"type": "string", "minLength": 1}, True),
        (UnboundedStringOutput, {"type": "string", "minLength": 1}, False),
        (BoundedStringOutput, {"type": "string", "minLength": 1, "maxLength": 10}, True),
        (BoundedStringOutput, {"type": "string", "minLength": 3}, False),
        (BoundedStringOutput, {"type": "string", "maxLength": 9}, False),
        (NestedNonemptyStringOutput, {"type": "string", "minLength": 1}, True),
        (MixedStringVariantOutput, {"type": "string", "minLength": 1}, False),
        (NonemptyStringOutput, {"type": "string", "pattern": ".+"}, False),
    ],
)
def test_result_binding_proves_only_contained_string_length_ranges(
    source_model: type[BaseModel], target_schema: dict[str, Any], expected: bool,
) -> None:
    path = ["result", "status"] if source_model in {
        NestedNonemptyStringOutput, MixedStringVariantOutput,
    } else ["status"]
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": path,
    })

    compatible = _result_binding_compatible(
        binding,
        target_schema,
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(source_model, mode="serialization"),
        "producer",
    )

    assert compatible is expected


@pytest.mark.parametrize(
    ("source_model", "target_schema", "expected"),
    [
        (NonNegativeIntegerOutput, {"type": "integer", "minimum": 0}, True),
        (PositiveIntegerOutput, {"type": "integer", "exclusiveMinimum": 0}, True),
        (BoundedIntegerOutput, {"type": "integer", "minimum": 0, "maximum": 10}, True),
        (UnderTenIntegerOutput, {"type": "integer", "exclusiveMaximum": 10}, True),
        (UnboundedIntegerOutput, {"type": "integer", "minimum": 0}, False),
        (NegativeIntegerOutput, {"type": "integer", "minimum": 0}, False),
        (NonNegativeIntegerOutput, {"type": "integer", "exclusiveMinimum": 0}, False),
        (BoundedIntegerOutput, {"type": "integer", "maximum": 9}, False),
        (BoundedIntegerOutput, {"type": "integer", "exclusiveMaximum": 10}, False),
    ],
)
def test_result_binding_proves_only_contained_numeric_ranges(
    source_model: type[BaseModel], target_schema: dict[str, Any], expected: bool,
) -> None:
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["revision"],
    })

    compatible = _result_binding_compatible(
        binding,
        target_schema,
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(source_model, mode="serialization"),
        "producer",
    )

    assert compatible is expected


@pytest.mark.parametrize(
    ("source_model", "expected"),
    [
        (NestedNonNegativeIntegerOutput, True),
        (MixedNumericVariantOutput, False),
    ],
)
def test_result_binding_numeric_ranges_resolve_refs_and_reject_unbounded_union_branches(
    source_model: type[BaseModel], expected: bool,
) -> None:
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["result", "revision"],
    })

    compatible = _result_binding_compatible(
        binding,
        {"type": "integer", "minimum": 0},
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(source_model, mode="serialization"),
        "producer",
    )

    assert compatible is expected


def test_result_binding_resolves_literal_values_through_source_refs() -> None:
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["result", "status"],
    })

    assert _result_binding_compatible(
        binding,
        {"type": "string", "enum": ["held", "stale"]},
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(ReferencedLiteralOutput, mode="serialization"),
        "producer",
    )


@pytest.mark.parametrize(
    ("source_model", "target_schema"),
    [
        (ObjectOutput, {"type": "object", "const": {}}),
        (ArrayOutput, {"type": "array", "enum": [[]]}),
    ],
)
def test_result_binding_rejects_unbounded_container_literals(
    source_model: type[BaseModel], target_schema: dict[str, Any],
) -> None:
    binding = parse_binding({
        "kind": "step_output", "step_key": "producer", "path": ["status"],
    })

    assert not _result_binding_compatible(
        binding,
        target_schema,
        schema_data_contract({"type": "object", "properties": {}}),
        model_data_contract(source_model, mode="serialization"),
        "producer",
    )


def tagged_result_schema(*tag_schemas: dict[str, Any]) -> dict[str, Any]:
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {"status": tag_schema},
                "required": ["status"],
                "additionalProperties": False,
            }
            for tag_schema in tag_schemas
        ]
    }


@pytest.mark.parametrize(
    ("schema", "status", "selected_index"),
    [
        (
            tagged_result_schema(
                {"type": "string", "enum": ["completed", "completed_with_warnings"]},
                {"type": "string", "enum": ["failed", "blocked"]},
            ),
            "completed_with_warnings",
            0,
        ),
        (
            tagged_result_schema(
                {"type": "string", "const": "completed"},
                {"type": "string", "enum": ["failed", "blocked"]},
            ),
            "blocked",
            1,
        ),
    ],
)
def test_tagged_one_of_selects_disjoint_enum_and_const_branches(
    schema: dict[str, Any], status: str, selected_index: int,
) -> None:
    binding = parse_binding({
        "kind": "object",
        "fields": {"status": {"kind": "constant", "value": status}},
    })

    assert _tagged_one_of_choice(binding, schema["oneOf"]) is schema["oneOf"][selected_index]


@pytest.mark.parametrize(
    ("schema", "status"),
    [
        (
            tagged_result_schema(
                {"type": "string", "enum": ["completed", "shared"]},
                {"type": "string", "enum": ["shared", "failed"]},
            ),
            "completed",
        ),
        (
            tagged_result_schema(
                {"type": "string", "enum": ["completed", "completed_with_warnings"]},
                {"type": "string", "const": "failed"},
            ),
            "unknown",
        ),
        (
            tagged_result_schema(
                {"type": "string", "enum": []},
                {"type": "string", "const": "failed"},
            ),
            "failed",
        ),
        (
            tagged_result_schema(
                {"type": "string", "const": "completed", "enum": ["failed"]},
                {"type": "string", "const": "failed"},
            ),
            "failed",
        ),
    ],
)
def test_tagged_one_of_refuses_overlapping_sets_and_unknown_values(
    schema: dict[str, Any], status: str,
) -> None:
    binding = parse_binding({
        "kind": "object",
        "fields": {"status": {"kind": "constant", "value": status}},
    })

    assert _tagged_one_of_choice(binding, schema["oneOf"]) is None


@pytest.mark.parametrize(
    ("schema", "status", "expected"),
    [
        (
            tagged_result_schema(
                {"type": "string", "enum": ["completed", "completed_with_warnings"]},
                {"type": "string", "enum": ["failed", "blocked"]},
            ),
            "completed",
            True,
        ),
        (
            tagged_result_schema(
                {"type": "string", "const": "completed"},
                {"type": "string", "enum": ["failed", "blocked"]},
            ),
            "failed",
            True,
        ),
        (
            tagged_result_schema(
                {"type": "string", "enum": ["completed", "shared"]},
                {"type": "string", "enum": ["shared", "failed"]},
            ),
            "completed",
            False,
        ),
        (
            tagged_result_schema(
                {"type": "string", "enum": ["completed", "completed_with_warnings"]},
                {"type": "string", "const": "failed"},
            ),
            "unknown",
            False,
        ),
    ],
)
def test_result_publication_requires_one_disjoint_tagged_branch(
    schema: dict[str, Any], status: str, expected: bool,
) -> None:
    binding = {
        "kind": "object",
        "fields": {"status": {"kind": "constant", "value": status}},
    }
    value = graph(
        [node("producer", LegacyOutcomeStep, entry=True)],
        output_schema=schema,
        result_rules=[{
            "outcome": "completed",
            "producer": "producer",
            "when_outcome": "",
            "binding": binding,
        }],
    )

    assert ("result_binding_schema" not in codes(value)) is expected


def test_map_body_candidates_use_graph_ownership_and_explain_exclusions() -> None:
    """Map authoring exposes only unattached ordinary steps without guessing from keys."""

    value = graph(
        [
            node("map", MapStep, {"target_step": "body", "items": "input"}, entry=True),
            node("body", TargetStep),
            node("free", TargetStep),
            node("connected", TargetStep),
            node("nested", MapStep, {"target_step": "missing", "items": "input"}),
        ],
        [edge("body", "connected")],
    )

    candidates = {item.key: item for item in value.map_body_candidates(GraphIdentity(client_key="node-map"))}

    assert candidates["free"].eligible is True
    assert candidates["body"].eligible is False
    assert candidates["connected"].eligible is False
    assert candidates["nested"].eligible is False
    assert candidates["body"].reason
    assert "connection" in candidates["connected"].reason.lower()


def test_operation_subject_contract_requires_matching_workflow_declaration() -> None:
    subject_node = node("subject", SubjectStep, entry=True)
    missing = graph([subject_node])
    matching = graph([subject_node], subject_declaration="notes.note")

    assert "subject_declaration_mismatch" in codes(missing)
    assert "subject_declaration_mismatch" not in codes(matching)


@pytest.mark.parametrize("items", ["input.", "unknown.items", "run..items"])
def test_map_item_expressions_reject_unreadable_paths_during_readiness(items: str) -> None:
    """The declaration owner rejects malformed expressions before runtime expansion."""

    value = graph(
        [
            node("map", MapStep, {"target_step": "body", "items": items}, entry=True),
            node("body", TargetStep),
        ]
    )

    assert "config_invalid" in codes(value)


def test_representative_note_party_and_internal_agent_graphs_are_ready() -> None:
    note = graph(
        [
            node("entry", NoteValidateForPublicationStep, entry=True),
            node("approval", GateStep, {"action": "approve-note", "slots": [{"assignee": "angee/role:admin#member"}]}),
            node("finalize", NotePublishStep),
        ],
        [edge("entry", "approval", "needs_review"), edge("approval", "finalize", "completed")],
        subject_declaration="notes.note",
    )
    parties = graph(
        [
            node("scan", DedupeScanStepImpl, {"limit": 50}, entry=True),
            node("gate", DedupeGateStepImpl),
            node("prepare", DedupeExecuteStepImpl, {"mode": "prepare"}),
            node("map", MapStep, {"target_step": "apply_unit", "items": "input"}),
            node("apply_unit", DedupeExecuteStepImpl, {"mode": "unit"}),
        ],
        [edge("scan", "gate", "found"), edge("gate", "prepare", "completed"), edge("prepare", "map", "prepared")],
    )
    agent = graph([node("session", AgentSessionStepImpl, entry=True)])

    assert note.diagnostics() == ()
    assert parties.diagnostics() == ()
    assert agent.diagnostics() == ()


def test_entry_executability_config_and_outcome_diagnostics_keep_exact_locations() -> None:
    value = graph(
        [node("abstract", UnimplementedStep, entry=True), node("wait", WaitStep)],
        [edge("abstract", "wait", "anything"), edge("wait", "abstract", "unexpected")],
    )

    diagnostics = value.diagnostics()
    assert {diagnostic.code for diagnostic in diagnostics} >= {
        "operation_not_executable",
        "config_invalid",
        "outcome_unsupported",
        "cycle",
    }
    assert any(diagnostic.location.field == "config.until" for diagnostic in diagnostics)
    assert any(diagnostic.location.field == "condition" for diagnostic in diagnostics)

    unknown = graph(
        [node("entry", LegacyOutcomeStep, entry=True), node("done", LegacyOutcomeStep)],
        [edge("entry", "done", "custom")],
    )
    assert "outcome_unsupported" not in codes(unknown)

    aliased_map_node = node("map", AliasedMapStep, {"target_step": "body", "items": "input"}, entry=True)
    aliased_map = graph(
        [
            GraphNode(
                aliased_map_node.identity,
                aliased_map_node.workflow_identity,
                aliased_map_node.key,
                aliased_map_node.is_entry,
                "map_alias",
                aliased_map_node.impl,
                aliased_map_node.config,
            ),
            node("body", LegacyOutcomeStep),
        ]
    )
    assert "operation_not_executable" in codes(aliased_map)


def test_binding_readiness_uses_declared_paths_ancestry_and_structured_locations() -> None:
    valid = graph(
        [
            node("source", SourceStep, entry=True),
            node("middle", LegacyOutcomeStep),
            node(
                "target",
                TargetStep,
                binding={
                    "kind": "object",
                    "fields": {"title": {"kind": "step_output", "step_key": "source", "path": ["payload"]}},
                },
            ),
        ],
        [edge("source", "middle"), edge("middle", "target")],
    )
    assert not ({"binding_source_unavailable", "binding_source_path", "binding_target_path"} & codes(valid))
    source = next(
        item for item in valid.input_sources(GraphIdentity(client_key="node-target")) if item.step_key == "source"
    )
    assert source.contract.matches_path(["payload"])

    invalid = graph(
        [
            node("source", SourceStep, entry=True),
            node(
                "target",
                TargetStep,
                binding={
                    "kind": "object",
                    "fields": {"a.b": {"kind": "step_output", "step_key": "future", "path": []}},
                },
            ),
            node("future", SourceStep),
        ],
        [edge("source", "target"), edge("target", "future")],
    )
    diagnostic = next(item for item in invalid.diagnostics() if item.code == "binding_source_unavailable")
    assert diagnostic.location.field == "input_binding"
    assert diagnostic.location.detail_path == ("fields", "a.b")

    malformed = graph([node("source", SourceStep, entry=True, binding={"kind": "step_output"})])
    assert any(item.code == "binding_invalid" and item.location.detail_path for item in malformed.diagnostics())

    incomplete = graph(
        [
            node(
                "source",
                SourceStep,
                entry=True,
                binding={
                    "kind": "object",
                    "fields": {"a.b[]/kind": {"kind": "array", "items": [{"kind": "constant", "value": None}, {}]}},
                },
            )
        ]
    )
    discriminator = next(item for item in incomplete.diagnostics() if item.code == "binding_invalid")
    assert discriminator.message == "Choose a value type."
    assert discriminator.location.detail_path == ("fields", "a.b[]/kind", "items", 1)

    constructed = graph(
        [
            node(
                "entry",
                LegacyOutcomeStep,
                entry=True,
                binding={
                    "kind": "object",
                    "fields": {
                        "dynamic.extra": {
                            "kind": "array",
                            "items": [{"kind": "constant", "value": 1}],
                        }
                    },
                },
            ),
            node(
                "declared",
                TargetStep,
                binding={"kind": "object", "fields": {"extra": {"kind": "constant", "value": True}}},
            ),
        ],
        [edge("entry", "declared")],
    )
    assert not {code for code in codes(constructed) if code.startswith("binding_")}


def test_map_binding_sources_are_owned_by_the_map_role() -> None:
    value = graph(
        [
            node("before", SourceStep, entry=True),
            node("map", MapStep, {"target_step": "body", "items": []}),
            node("body", TargetStep, binding={"kind": "map_item", "path": []}),
            node("after", TargetStep, binding={"kind": "map_item", "path": []}),
        ],
        [edge("before", "map"), edge("map", "after")],
    )

    body_sources = value.input_sources(GraphIdentity(client_key="node-body"))
    assert {(source.kind, source.step_key) for source in body_sources} == {
        ("workflow_input", None),
        ("step_output", "before"),
        ("map_item", None),
    }
    assert any(
        item.code == "binding_source_unavailable" and item.location.key.client_key == "node-after"
        for item in value.diagnostics()
    )


@pytest.mark.parametrize("constructed_input", [False, True])
def test_map_item_paths_follow_the_declared_input_source(constructed_input: bool) -> None:
    class CollectionOutput(BaseModel):
        items: list[DeclaredInput]

    class CollectionStep(LegacyOutcomeStep):
        key = "collection"
        output_model = CollectionOutput

    selected: dict[str, Any] = {"kind": "step_output", "step_key": "before", "path": []}
    if constructed_input:
        selected = {
            "kind": "object",
            "fields": {"items": {**selected, "path": ["items"]}},
        }
    value = graph(
        [
            node("before", CollectionStep, entry=True),
            node("map", MapStep, {"target_step": "body", "items": "input.items"}, binding=selected),
            node(
                "body", TargetStep,
                binding={
                    "kind": "object",
                    "fields": {
                        "title": {"kind": "map_item", "path": ["title"]},
                        "invalid": {"kind": "map_item", "path": ["undeclared"]},
                    },
                },
            ),
        ],
        [edge("before", "map")],
    )
    sources = value.input_sources(GraphIdentity(client_key="node-body"))
    contract = next(source.contract for source in sources if source.kind == "map_item")
    assert contract.matches_path(("title",))
    assert not contract.matches_path(("undeclared",))
    diagnostics = [item for item in value.diagnostics() if item.location.field == "input_binding"]
    assert len(diagnostics) == 1
    assert diagnostics[0].location.detail_path == ("fields", "invalid", "path")


def test_source_catalogue_never_selects_self_or_collapses_duplicate_keys() -> None:
    cycle = graph(
        [node("left", SourceStep, entry=True), node("target", TargetStep)],
        [edge("left", "target"), edge("target", "left")],
    )
    assert all(
        source.node_identity != GraphIdentity(client_key="node-target")
        for source in cycle.input_sources(GraphIdentity(client_key="node-target"))
    )

    duplicate = graph(
        [
            node("entry", SourceStep, entry=True),
            node("same", SourceStep),
            GraphNode(
                GraphIdentity(client_key="node-same-2"),
                GraphIdentity(client_key="workflow-1"),
                "same",
                False,
                SourceStep.key,
                SourceStep,
                {},
                "Same again",
            ),
            node("target", TargetStep, binding={"kind": "step_output", "step_key": "same", "path": []}),
        ],
        [edge("entry", "same"), edge("same", "target"), edge("entry", "target")],
    )
    assert "node_key_duplicate" in codes(duplicate)
    assert "binding_source_unavailable" in codes(duplicate)


@pytest.mark.parametrize(
    ("nodes", "edges", "expected"),
    [
        ([node("map", MapStep, {"target_step": "missing", "items": "input"}, entry=True)], [], "map_target_missing"),
        ([node("map", MapStep, {"target_step": "map", "items": "input"}, entry=True)], [], "map_target_self"),
        (
            [
                node("outer", MapStep, {"target_step": "inner", "items": "input"}, entry=True),
                node("inner", MapStep, {"target_step": "body", "items": "input"}),
                node("body", LegacyOutcomeStep),
            ],
            [],
            "map_target_nested",
        ),
        (
            [
                node("left", MapStep, {"target_step": "body", "items": "input"}, entry=True),
                node("right", MapStep, {"target_step": "body", "items": "input"}),
                node("body", LegacyOutcomeStep),
            ],
            [edge("left", "right")],
            "map_target_shared",
        ),
        (
            [
                node("map", MapStep, {"target_step": "body", "items": "input"}, entry=True),
                node("body", LegacyOutcomeStep),
            ],
            [edge("body", "map")],
            "map_body_edge",
        ),
    ],
)
def test_map_topology_rejects_engine_unsupported_shapes(
    nodes: list[GraphNode], edges: list[GraphEdge], expected: str
) -> None:
    assert expected in codes(graph(nodes, edges))


def test_reachability_excludes_unique_detached_map_body_and_cycles_are_not_ready() -> None:
    ready = graph(
        [
            node("entry", LegacyOutcomeStep, entry=True),
            node("map", MapStep, {"target_step": "body", "items": "input"}),
            node("body", LegacyOutcomeStep),
        ],
        [edge("entry", "map")],
    )
    assert "unreachable" not in codes(ready)

    disconnected = graph([node("entry", LegacyOutcomeStep, entry=True), node("lost", LegacyOutcomeStep)])
    assert "unreachable" in codes(disconnected)
    cyclic = graph(
        [node("entry", LegacyOutcomeStep, entry=True), node("other", LegacyOutcomeStep)],
        [edge("entry", "other"), edge("other", "entry")],
    )
    assert "cycle" in codes(cyclic)


def test_literal_map_capacity_rejects_only_each_proven_batch() -> None:
    branches = graph(
        [
            node("entry", MapStep, {"target_step": "first_body", "items": [1, 2]}, entry=True),
            node("next", MapStep, {"target_step": "second_body", "items": [1, 2]}),
            node("first_body", LegacyOutcomeStep),
            node("second_body", LegacyOutcomeStep),
        ],
        [edge("entry", "next")],
        max_steps=3,
    )
    assert "map_capacity" not in codes(branches)

    overflow = graph(
        [
            node("entry", MapStep, {"target_step": "body", "items": [1, 2, 3]}, entry=True),
            node("body", LegacyOutcomeStep),
        ],
        max_steps=3,
    )
    assert "map_capacity" in codes(overflow)

    dynamic = graph(
        [
            node("entry", MapStep, {"target_step": "body", "items": "input"}, entry=True),
            node("body", LegacyOutcomeStep),
        ],
        max_steps=1,
    )
    assert "map_capacity" not in codes(dynamic)


def test_validate_raises_all_diagnostics_as_django_field_paths() -> None:
    value = graph([node("wait", WaitStep, entry=True)])
    with pytest.raises(ValidationError) as caught:
        value.validate()
    assert "node.node-wait.config.until" in caught.value.message_dict


def test_structural_diagnostics_retain_identity_ownership_and_invalid_operations() -> None:
    duplicate = node("same", LegacyOutcomeStep, entry=True)
    foreign = GraphNode(
        GraphIdentity(client_key="node-foreign"),
        GraphIdentity(client_key="workflow-2"),
        "same",
        False,
        "missing",
        None,
        {},
    )
    absent = GraphIdentity(client_key="absent")
    owner = GraphIdentity(client_key="workflow-1")
    broken = GraphEdge(GraphIdentity(client_key="edge-broken"), owner, duplicate.identity, absent, "same", "same", "")
    repeated = GraphEdge(GraphIdentity(client_key="edge-repeat"), owner, duplicate.identity, absent, "same", "same", "")

    result = codes(graph([duplicate, foreign], [broken, repeated]))
    assert {
        "node_key_duplicate",
        "node_workflow_mismatch",
        "operation_unknown",
        "edge_target_missing",
        "edge_duplicate",
    } <= result


def test_from_rows_uses_explicit_distinct_prospective_client_identities() -> None:
    workflow = SimpleNamespace(pk=None, correlation="draft", max_steps=10)
    first = SimpleNamespace(
        pk=None,
        correlation="new-a",
        workflow_id=None,
        key="",
        is_entry=True,
        step_class="legacy",
        config={},
        resolve_impl=lambda _field: LegacyOutcomeStep,
    )
    second = SimpleNamespace(
        pk=None,
        correlation="new-b",
        workflow_id=None,
        key="",
        is_entry=False,
        step_class="legacy",
        config={},
        resolve_impl=lambda _field: LegacyOutcomeStep,
    )
    value = WorkflowGraph.from_rows(
        workflow,
        [first, second],
        [],
        identity=lambda row: GraphIdentity(client_key=row.correlation),
        owner_identity=lambda _row: GraphIdentity(client_key="draft"),
    )

    locations = {diagnostic.location.key for diagnostic in value.diagnostics() if diagnostic.code == "node_key_blank"}
    assert {str(location) for location in locations} == {"new-a", "new-b"}
