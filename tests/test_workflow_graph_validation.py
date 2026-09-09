"""Publication-readiness tests for the immutable workflow graph owner."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel

from angee.workflows.graph import GraphEdge, GraphIdentity, GraphNode, WorkflowGraph
from angee.workflows.steps import GateStep, HandlerStep, MapStep, StepImpl, StepResult, WaitStep
from angee.workflows_agents.steps import AgentSessionStepImpl
from angee.workflows_parties.steps import DedupeExecuteStepImpl, DedupeGateStepImpl, DedupeScanStepImpl
from example.notes.steps import NotePublishStep, NoteValidateForPublicationStep


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


def graph(nodes: list[GraphNode], edges: list[GraphEdge] | None = None, *, max_steps: int = 100) -> WorkflowGraph:
    return WorkflowGraph(GraphIdentity(client_key="workflow-1"), max_steps, tuple(nodes), tuple(edges or []))


def codes(value: WorkflowGraph) -> set[str]:
    return {diagnostic.code for diagnostic in value.diagnostics()}


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
    matching = WorkflowGraph(
        missing.identity,
        missing.max_steps,
        missing.nodes,
        missing.edges,
        "notes.note",
    )

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
        [node("abstract", HandlerStep, entry=True), node("wait", WaitStep)],
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
