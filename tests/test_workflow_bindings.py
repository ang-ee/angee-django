"""Contracts for typed workflow input binding evaluation."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from angee.workflows.attempts import AttemptInput, JsonPresence
from angee.workflows.bindings import (
    BindingContext,
    SourceValue,
    UnavailableSource,
    binding_error_details,
    binding_error_locations,
    evaluate_binding,
    parse_binding,
)


def context() -> BindingContext:
    return BindingContext(
        workflow_input=SourceValue(
            JsonPresence(True, {"user.name": "Ada", "items": [None]}), {"kind": "workflow_input", "run_id": 2}
        ),
        step_outputs={
            "validate": SourceValue(
                JsonPresence(True, {"payload": {"ok": True}}),
                {"kind": "step_output", "step_run_id": 4, "attempt_id": 7},
            )
        },
        map_item=UnavailableSource("map_scope", "No current Map item is available.", {"kind": "map_item"}),
    )


def test_binding_round_trip_and_constant_json_is_opaque() -> None:
    raw = {"kind": "constant", "value": {"kind": "step_output", "value": [1, None]}}
    binding = parse_binding(raw)
    assert binding.model_dump(mode="json") == raw
    assert evaluate_binding(binding, context()).value == JsonPresence(True, raw["value"])


def test_evaluation_returns_owned_json_values() -> None:
    constant = parse_binding({"kind": "constant", "value": {"nested": [1]}})
    source_context = context()
    first_constant = evaluate_binding(constant, source_context)
    first_source = evaluate_binding(
        parse_binding({"kind": "step_output", "step_key": "validate", "path": ["payload"]}),
        source_context,
    )
    assert first_constant.value is not None and first_source.value is not None
    first_constant.value.value["nested"].append(2)
    first_source.value.value["ok"] = False

    assert evaluate_binding(constant, source_context).value == JsonPresence(True, {"nested": [1]})
    assert evaluate_binding(
        parse_binding({"kind": "step_output", "step_key": "validate", "path": ["payload"]}),
        source_context,
    ).value == JsonPresence(True, {"ok": True})


@pytest.mark.parametrize("value", [math.inf, math.nan, object()])
def test_constant_rejects_values_outside_finite_json(value: object) -> None:
    with pytest.raises(ValidationError):
        parse_binding({"kind": "constant", "value": value})


def test_paths_distinguish_dotted_keys_indexes_missing_and_present_null() -> None:
    dotted = evaluate_binding(parse_binding({"kind": "workflow_input", "path": ["user.name"]}), context())
    present_null = evaluate_binding(parse_binding({"kind": "workflow_input", "path": ["items", 0]}), context())
    missing = evaluate_binding(parse_binding({"kind": "workflow_input", "path": ["items", 1]}), context())
    wrong_type = evaluate_binding(parse_binding({"kind": "workflow_input", "path": ["items", 0, "name"]}), context())
    assert dotted.value == JsonPresence(True, "Ada")
    assert present_null.value == JsonPresence(True, None)
    assert missing.diagnostics[0].code == "path_missing"
    assert missing.diagnostics[0].source_path == ("items", 1)
    assert wrong_type.diagnostics[0].code == "path_type"


def test_construction_reports_all_children_and_retains_exact_provenance() -> None:
    binding = parse_binding(
        {
            "kind": "object",
            "fields": {
                "good": {"kind": "step_output", "step_key": "validate", "path": ["payload", "ok"]},
                "missing": {"kind": "step_output", "step_key": "gone", "path": []},
                "map": {"kind": "map_item", "path": []},
            },
        }
    )
    result = evaluate_binding(binding, context())
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["map_scope", "source_missing"]
    assert [diagnostic.path for diagnostic in result.diagnostics] == [
        ("fields", "map"),
        ("fields", "missing"),
    ]

    reversed_result = evaluate_binding(
        parse_binding(
            {
                "kind": "object",
                "fields": {
                    "map": {"kind": "map_item", "path": []},
                    "missing": {"kind": "step_output", "step_key": "gone", "path": []},
                },
            }
        ),
        context(),
    )
    assert [diagnostic.path for diagnostic in reversed_result.diagnostics] == [
        ("fields", "map"),
        ("fields", "missing"),
    ]

    successful = evaluate_binding(
        parse_binding(
            {"kind": "array", "items": [{"kind": "step_output", "step_key": "validate", "path": ["payload"]}]}
        ),
        context(),
    )
    assert successful.value == JsonPresence(True, [{"ok": True}])
    assert successful.provenance == {
        "kind": "array",
        "items": [{"kind": "step_output", "step_run_id": 4, "attempt_id": 7, "path": ["payload"]}],
    }


def test_absent_source_is_not_present_null() -> None:
    supplied = context()
    absent = BindingContext(
        workflow_input=SourceValue(JsonPresence(False, None), {"kind": "workflow_input", "run_id": 2}),
        step_outputs=supplied.step_outputs,
        map_item=supplied.map_item,
    )
    result = evaluate_binding(parse_binding({"kind": "workflow_input", "path": []}), absent)
    assert result.value is None
    assert result.diagnostics[0].code == "source_absent"


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "unknown"},
        {"kind": "workflow_input", "path": [True]},
        {"kind": "workflow_input", "path": [-1]},
        {"kind": "workflow_input", "path": [], "extra": 1},
        {"kind": "step_output", "step_key": "", "path": []},
        {"kind": "step_output", "step_key": "white space", "path": []},
        {"kind": "step_output", "step_key": "punctuation!", "path": []},
    ],
)
def test_binding_grammar_rejects_ambiguous_or_unknown_shapes(raw: object) -> None:
    with pytest.raises(ValidationError):
        parse_binding(raw)  # type: ignore[arg-type]


def test_attempt_input_constructor_remains_compatible_with_shared_presence() -> None:
    value = AttemptInput(present=True, value=None, provenance={"kind": "constant"})
    assert isinstance(value, JsonPresence)
    assert value.present and value.value is None


@pytest.mark.parametrize("step_key", ["hyphen-key", "under_score", "ASCII123"])
def test_step_output_keys_use_the_persisted_slug_grammar(step_key: str) -> None:
    binding = parse_binding({"kind": "step_output", "step_key": step_key, "path": []})
    assert binding.step_key == step_key


def test_parse_error_locations_remove_only_actual_union_tags() -> None:
    raw = {
        "kind": "object",
        "fields": {
            "object": {
                "kind": "object",
                "fields": {
                    "fields": {"kind": "step_output", "step_key": "source", "path": [True]},
                },
            }
        },
    }
    with pytest.raises(ValidationError) as caught:
        parse_binding(raw)

    assert set(binding_error_locations(raw, caught.value)) == {
        ("fields", "object", "fields", "fields", "path", 0),
    }

    extra = {"kind": "constant", "value": 1, "constant": 2}
    with pytest.raises(ValidationError) as extra_error:
        parse_binding(extra)
    assert binding_error_locations(extra, extra_error.value) == (("constant",),)

    invalid_child = {"kind": "object", "fields": {"kind": {"kind": "missing"}}}
    with pytest.raises(ValidationError) as child_error:
        parse_binding(invalid_child)
    assert binding_error_locations(invalid_child, child_error.value) == (("fields", "kind"),)
    assert binding_error_details(invalid_child, child_error.value) == (
        (("fields", "kind"), "Choose a value type."),
    )
