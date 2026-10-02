"""Map publication, typed item bindings and the graph's bounded body planning."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict

from angee.base.jsonschema import schemas_match, validator
from angee.workflows.definition import Definition
from angee.workflows.maps import Map, MapItem
from angee.workflows.reviews import ReviewStep
from angee.workflows.steps import EmptyOutput, Step


class Value(BaseModel):
    """A strict item shared by the source, body and typed collector."""

    model_config = ConfigDict(extra="forbid")
    value: int


class Values(BaseModel):
    """A source's explicitly typed list."""

    items: list[Value]


class Configuration(BaseModel):
    """A body configuration resolved through its generated key."""

    amount: int = 1


class Produce(Step[None, Values, None]):
    """Supply a typed list without running a database step."""

    key = "map_produce"


class Echo(Step[Value, Value, Configuration]):
    """Declare the body contract for graph-only proofs."""

    key = "map_echo"


class Collect(Step[list[MapItem[Value]], None, None]):
    """Consume the engine-owned generic result contract directly."""

    key = "map_collect"


@pytest.fixture(autouse=True)
def steps(register_step):
    """Restore every registered class after the individual test."""
    for step in (Map, Produce, Echo, Collect):
        register_step(step)


def document(*, body=None, source=True):
    """Declare one map with either typed producer items or external run input."""
    return {
        "nodes": {
            **({"prepare": {"step": Produce.key, "next": {"done": "each"}}} if source else {}),
            "each": {
                "step": "map", "body": body or {"step": Echo.key},
                **({"input": {"items": {"from": "prepare", "path": ["items"]}}} if source else {}),
            },
        },
        "results": [{"from": "each"}],
    }


def row(key, *, status="succeeded", index=0, items=None, output=None):
    """Provide only persisted facts consulted by definition planning/binding."""
    return SimpleNamespace(
        node_key=key, map_index=index, status=status, outcome="error" if status == "failed" else "done",
        input={"items": items or []}, output=output or {}, waiting_kind="map" if status == "waiting" else "",
    )


def test_nested_body_resolves_step_config_and_typed_result_contract():
    """Body keys remain internal while the map's output carries its exact type."""
    data = document(body={"step": Echo.key, "config": {"amount": 4}})
    data["nodes"]["each"]["next"] = {"done": "collect", "failed": "collect"}
    data["nodes"]["collect"] = {"step": Collect.key}
    definition, issues = Definition.check(data)
    assert issues == []
    assert definition.step("each.body") is Echo
    assert definition.node("each.body").config == {"amount": 4}
    assert schemas_match(definition.output_schema("each"), Collect.input_schema())
    assert definition.predecessors == {"prepare": {}, "each": {"prepare": {"done"}},
                                       "collect": {"each": {"done", "failed"}}}
    with pytest.raises(KeyError):
        definition.node("each.other")


def test_body_input_binds_item_ancestor_and_workflow_input():
    """A body sees its item and permitted external sources through one binder."""
    data = document(body={"step": Echo.key, "input": {"value": {"from": "item", "path": ["value"]}}})
    definition, issues = Definition.check(data)
    assert issues == []
    rows = [row("prepare", output={"items": [{"value": 3}, {"value": 7}]}),
            row("each", status="waiting", items=[{"value": 3}, {"value": 7}])]
    assert definition.input_for("each.body", {}, rows, map_index=1) == {"value": 7}
    for source, path, run_input in (("prepare", ["items", 0, "value"], {}), ("input", ["number"], {"number": 8})):
        data["nodes"]["each"]["body"]["input"]["value"] = {"from": source, "path": path}
        definition, issues = Definition.check(data)
        assert issues == []
        assert definition.input_for("each.body", run_input, rows, map_index=1) == {"value": 8 if run_input else 3}


@pytest.mark.parametrize("body", [{"step": Echo.key}, {
    "step": Echo.key, "input": {"value": {"from": "item", "path": ["number"]}},
}])
def test_external_items_are_validated_at_every_index(body):
    """Body requirements constrain all external list elements, not only index zero."""
    definition, issues = Definition.check(document(body=body, source=False))
    assert issues == []
    field = "number" if "input" in body else "value"
    assert definition.validate_input({"items": [{field: 2}, {field: 4}]}) == {"items": [{field: 2}, {field: 4}]}
    with pytest.raises(ValidationError):
        definition.validate_input({"items": [{field: 2}, {field: "invalid"}]})


def test_literal_items_are_checked_against_the_body_contract():
    """The publication boundary rejects malformed literal items in any position."""
    data = document(source=False)
    data["nodes"]["each"]["input"] = {"items": {"value": [{"value": 2}, {"value": "invalid"}]}}
    definition, issues = Definition.check(data)
    assert definition is not None
    assert any(issue.code == "binding" and issue.path == ["nodes", "each", "input", "items"] for issue in issues)


@pytest.mark.parametrize("source,path", [("each", []), ("item", ["missing"]), ("item", ["value", "nested"])])
def test_invalid_body_binding_is_a_publish_issue(source, path):
    """A body cannot consume the unsettled map or an unproved element path."""
    _, issues = Definition.check(document(body={
        "step": Echo.key, "input": {"value": {"from": source, "path": path}},
    }))
    assert any(issue.code == "binding" and issue.path[:3] == ["nodes", "each", "body"] for issue in issues)


def test_item_binding_is_not_available_to_an_ordinary_node():
    """The reserved source is available only inside a body."""
    _, issues = Definition.check({"nodes": {"plain": {
        "step": Echo.key, "input": {"value": {"from": "item", "path": ["value"]}},
    }}})
    assert any(issue.code == "binding" for issue in issues)


def test_nested_map_is_rejected_at_publication_and_unknown_body_blocks_draft():
    """Nested maps parse for editing; unknown implementations cannot be saved."""
    definition, issues = Definition.check(document(body={"step": "map"}))
    assert definition is not None
    assert any(issue.code == "body" and not issue.blocks_draft for issue in issues)
    _, issues = Definition.check(document(body={"step": "unknown_body"}))
    assert any(issue.code == "unknown_step" and issue.blocks_draft for issue in issues)


def test_body_has_no_graph_edges_in_the_declaration_schema():
    """The studio and server share the nested body shape from the Pydantic owner."""
    data = document(body={"step": Echo.key, "next": {"done": "each"}})
    assert not validator(Definition.model_json_schema()).is_valid(data)
    definition, issues = Definition.check(data)
    assert definition is None and all(issue.blocks_draft for issue in issues)


def test_planner_bounds_open_items_and_reuses_map_rank():
    """Settled items free slots; a waiting item still occupies its admitted slot."""
    definition, issues = Definition.check(document())
    assert issues == []
    parent = row("each", status="waiting", items=[{}, {}, {}, {}])
    rows = [row("prepare"), parent]
    planned = definition.ready_nodes(rows, map_concurrency=2)
    assert [(item.node_key, item.map_index, item.rank) for item in planned] == [
        ("each.body", 0, 1), ("each.body", 1, 1),
    ]
    rows.extend([row("each.body", status="waiting"), row("each.body", index=1)])
    planned = definition.ready_nodes(rows, map_concurrency=2)
    assert [(item.node_key, item.map_index) for item in planned] == [("each.body", 2)]
    rows.extend([row("each.body", index=2), row("each.body", index=3)])
    rows[2].status = "succeeded"
    planned = definition.ready_nodes(rows, map_concurrency=2)
    assert [(item.node_key, item.existing, item.rank) for item in planned] == [("each", True, 1)]


def test_body_failure_routing_is_owned_by_the_containing_map():
    """Unrouted failure stops siblings; a handled failed outcome collects evidence."""
    data = document()
    definition, _ = Definition.check(data)
    failed = row("each.body", status="failed", index=2)
    assert definition.unrouted_failure([failed])
    assert definition.retry_allowed("each.body")
    data["nodes"]["each"]["next"] = {"failed": "collect"}
    data["nodes"]["collect"] = {"step": Collect.key}
    definition, issues = Definition.check(data)
    assert issues == []
    assert not definition.unrouted_failure([failed])
    assert not definition.retry_allowed("each.body")


@pytest.mark.parametrize("concurrency", [0, -1, 1.5, "2", True])
def test_planner_rejects_invalid_concurrency(concurrency):
    """An invalid setting fails instead of silently stranding a waiting map."""
    definition, _ = Definition.check(document())
    with pytest.raises(ValueError, match="positive"):
        definition.ready_nodes([], map_concurrency=concurrency)


def test_required_body_outcomes_are_collected_without_graph_edges(register_step):
    """A required control edge applies to a graph node, never a nested body."""
    class Required(Echo):
        key = "map_required"

        @classmethod
        def required_outcomes(cls, config):
            return {"done"}

    register_step(Required)
    _, issues = Definition.check(document(body={"step": Required.key}))
    assert issues == []
    _, issues = Definition.check({"nodes": {"plain": {"step": Required.key}}})
    assert any(issue.code == "required_outcome" for issue in issues)


def test_items_source_requires_a_list_even_when_body_is_untyped(register_step):
    """The map's native list requirement survives element-schema specialization."""
    class Untyped(Step[None, None, None]):
        key = "map_untyped"

    register_step(Untyped)
    data = document(body={"step": Untyped.key})
    data["nodes"]["prepare"]["step"] = Echo.key
    data["nodes"]["each"]["input"]["items"]["path"] = ["value"]
    _, issues = Definition.check(data)
    assert any(issue.code == "input" and "list" in issue.message for issue in issues)


def test_review_body_output_includes_typed_unanswered_closures(register_step):
    """A collector must admit both applied output and the review's empty outcomes."""
    class TypedReview(ReviewStep[Value, Value, None, None]):
        key = "map_typed_review"

    class ReviewCollector(Step[list[MapItem[Value | EmptyOutput]], None, None]):
        key = "map_review_collector"

    register_step(TypedReview)
    register_step(ReviewCollector)
    data = document(body={"step": TypedReview.key})
    data["nodes"]["each"]["next"] = {"done": "collect"}
    data["nodes"]["collect"] = {"step": ReviewCollector.key}
    definition, issues = Definition.check(data)
    assert issues == []
    contract = validator(definition.output_schema("each"))
    assert contract.is_valid([{"index": 0, "outcome": "expired", "output": {}}])
    assert contract.is_valid([{"index": 0, "outcome": "done", "output": {"value": 4}}])
    data["nodes"]["collect"]["step"] = Collect.key
    _, issues = Definition.check(data)
    assert any(issue.code == "input" and issue.node == "collect" for issue in issues)
