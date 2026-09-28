"""Graph, binding, publication and typed step contract proofs."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import override_settings
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from angee.base.impl import ImplBase
from angee.workflows.bindings import SourceBinding
from angee.workflows.definition import Definition
from angee.workflows.states import ERROR_OUTCOME
from angee.workflows.steps import Step, Wait, resolve_step
from angee.workflows.testing.models import Workflow


class Payload(BaseModel):
    """A shared neutral data contract."""

    model_config = ConfigDict(extra="forbid")
    value: int


class TextPayload(BaseModel):
    """An incompatible data contract for binding diagnostics."""

    value: str


class Configuration(BaseModel):
    """Config with a typed default."""

    amount: int = 3


class Echo(Step[Payload, Payload, None]):
    """A graph-test step with alternative success outcomes."""

    key = "echo"
    outcomes = {"done": "Done", "alternate": "Alternate"}


class TextEcho(Step[TextPayload, TextPayload, None]):
    """A step declaring an incompatible output shape."""

    key = "text_echo"


class Configured(Step[Payload, Payload, Configuration]):
    """A step whose generic config uses the inherited owner."""

    key = "configured"


class Dynamic(Step[Payload, Payload, Configuration]):
    """Select authored outcomes from config without restating built-in failures."""

    key = "dynamic"

    @classmethod
    def outcomes_for(cls, config):
        """Return only the outcome selected by this declaration."""
        return {"added": "Added"} if config.amount else {"zero": "Zero"}


@pytest.fixture(autouse=True)
def steps():
    """Register classes through the production settings registry."""
    with override_settings(
        ANGEE_WORKFLOW_STEP_CLASSES={
            "echo": "tests.test_workflows_definition.Echo",
            "text_echo": "tests.test_workflows_definition.TextEcho",
            "configured": "tests.test_workflows_definition.Configured",
            "dynamic": "tests.test_workflows_definition.Dynamic",
        }
    ):
        yield


def row(key, status="succeeded", outcome="done", output=None):
    """Supply exactly the row values the pure graph consumes."""
    return SimpleNamespace(
        node_key=key,
        map_index=0,
        status=status,
        outcome=outcome,
        output=({} if status == "failed" else {"value": 1}) if output is None else output,
    )


def graph(nodes, results=None):
    """Parse ordinary declaration data with a default fixture step."""
    return Definition.model_validate(
        {"nodes": {key: {"step": "echo", **value} for key, value in nodes.items()}, "results": results or []}
    )


def planned(definition, rows):
    """Expose planner output without coupling assertions to its carrier."""
    return [(item.node_key, item.status) for item in definition.ready_nodes(rows)]


def test_linear_entry_and_next_node():
    """Linear entry and next node."""
    definition = graph({"start": {"next": {"done": "finish"}}, "finish": {}})
    assert definition.issues() == []
    assert definition.entry == "start"
    assert planned(definition, []) == [("start", "ready")]
    assert planned(definition, [row("start", "running")]) == []
    assert planned(definition, [row("start")]) == [("finish", "ready")]
    assert planned(definition, [row("start"), row("finish", "ready")]) == []


def test_branching_creates_live_and_skipped_rows():
    """Branching creates live and skipped rows."""
    definition = graph({"choose": {"next": {"done": "left", "alternate": "right"}}, "left": {}, "right": {}})
    assert planned(definition, [row("choose")]) == [("left", "ready"), ("right", "skipped")]


def test_fanout_creates_both_branches():
    """Fanout creates both branches."""
    definition = graph({"start": {"next": {"done": ["left", "right"]}}, "left": {}, "right": {}})
    assert planned(definition, [row("start")]) == [("left", "ready"), ("right", "ready")]


@pytest.mark.parametrize("join,status", [("any", "ready"), ("all", "skipped")])
def test_join_waits_for_every_source_and_counts_live_sources(join, status):
    """Join waits for every source and counts live sources."""
    definition = graph(
        {
            "start": {"next": {"done": ["left", "right"]}},
            "left": {"next": {"done": "finish"}},
            "right": {"next": {"done": "finish"}},
            "finish": {"join": join},
        }
    )
    assert planned(definition, [row("start"), row("left"), row("right", "running")]) == []
    assert planned(definition, [row("start"), row("left"), row("right", "skipped")]) == [("finish", status)]
    assert planned(definition, [row("start"), row("left"), row("right")]) == [("finish", "ready")]


def test_all_join_groups_alternative_edges_from_one_source():
    """All join groups alternative edges from one source."""
    definition = graph({"start": {"next": {"done": "finish", "alternate": "finish"}}, "finish": {"join": "all"}})
    assert planned(definition, [row("start")]) == [("finish", "ready")]


def test_skip_propagation_in_one_plan_does_not_lose_join():
    """Skip propagation in one plan does not lose join."""
    definition = graph(
        {
            "start": {"next": {"done": "live", "alternate": "dead"}},
            "live": {"next": {"done": "finish"}},
            "dead": {"next": {"done": "later"}},
            "later": {"next": {"done": "finish"}},
            "finish": {},
        }
    )
    assert planned(definition, [row("start"), row("live")]) == [
        ("dead", "skipped"),
        ("later", "skipped"),
        ("finish", "ready"),
    ]


def test_unrouted_outcome_ends_branch_and_propagates_skips():
    """Unrouted outcome ends branch and propagates skips."""
    definition = graph({"start": {"next": {"done": "later"}}, "later": {"next": {"done": "finish"}}, "finish": {}})
    assert planned(definition, [row("start", outcome="alternate")]) == [("later", "skipped"), ("finish", "skipped")]


@pytest.mark.parametrize("routed,status", [(True, "ready"), (False, "skipped")])
def test_failed_step_routes_only_error_edge(routed, status):
    """Failed step routes only error edge."""
    definition = graph(
        {
            "start": {"next": {"error" if routed else "done": "recover"}},
            "recover": {"input": {"value": {"value": 0}}},
        }
    )
    assert definition.issues() == []
    assert planned(definition, [row("start", "failed", "error")]) == [("recover", status)]


@pytest.mark.parametrize("key", ["Upper", "has-dash", "has.dot", "a" * 64, "1first", ""])
def test_node_key_grammar_blocks_drafts(key):
    """Node key grammar blocks drafts."""
    definition, issues = Definition.check({"nodes": {key: {"step": "echo"}}})
    assert definition is None
    assert issues and all(issue.blocks_draft and issue.code == "parse" for issue in issues)


def test_incomplete_draft_has_typed_publish_issues_but_is_saveable():
    """Incomplete draft has typed publish issues but is saveable."""
    definition, issues = Definition.check({"nodes": {"first": {"step": "echo"}, "second": {"step": "echo"}}})
    assert definition is not None
    assert [issue.code for issue in issues] == ["entry"]
    assert not any(issue.blocks_draft for issue in issues)
    assert issues[0].model_dump() == {
        "node": None,
        "path": [],
        "code": "entry",
        "message": "A workflow requires exactly one entry node.",
    }


def test_unknown_step_blocks_draft():
    """Unknown step blocks draft."""
    definition, issues = Definition.check({"nodes": {"start": {"step": "missing"}}})
    assert definition is not None
    assert any(issue.code == "unknown_step" and issue.blocks_draft for issue in issues)


def test_definition_resolves_each_node_once_across_contract_consumers(monkeypatch):
    """Graph validation, admission and schemas share the definition's resolved classes."""
    resolved = []

    def resolve(key):
        resolved.append(key)
        return resolve_step(key)

    monkeypatch.setattr("angee.workflows.definition.resolve_step", resolve)
    definition = graph({"start": {"next": {"done": "finish"}}, "finish": {}}, [{"from": "finish"}])
    assert resolved == []
    assert definition.step("start") is Echo
    assert definition.step("start") is Echo
    assert resolved == ["echo"]
    assert definition.issues() == []
    assert definition.validate_input({"value": 1}) == {"value": 1}
    assert definition.input_for("finish", {}, [row("start")]) == {"value": 1}
    assert definition.result_schema(definition.results[0]) == Echo.output_schema()
    assert definition.input_schema is definition.input_schema
    assert resolved == ["echo", "echo"]


def test_missing_step_is_lazy_and_does_not_hide_other_diagnostics():
    """An incomplete draft's missing implementation leaves known node contracts usable."""
    definition = graph({"start": {"next": {"unknown": "missing"}}, "missing": {"step": "uninstalled"}})
    assert definition.step("start") is Echo
    assert definition.input_for("start", {"value": 1}, []) == {"value": 1}
    issues = definition.issues()
    assert any(issue.code == "unknown_step" and issue.node == "missing" for issue in issues)
    assert any(issue.code == "outcome" and issue.node == "start" for issue in issues)


@pytest.mark.parametrize("key", ["input", "item"])
def test_binding_source_names_are_reserved_at_publish(key):
    """Binding source names are reserved at publish."""
    definition, issues = Definition.check({"nodes": {key: {"step": "echo"}}})
    assert definition is not None
    assert any(issue.code == "reserved_node" and not issue.blocks_draft for issue in issues)


def test_publish_checks_step_subject_against_workflow_identity(monkeypatch):
    """Publish checks step subject against workflow identity."""
    monkeypatch.setattr(Echo, "subject", "knowledge.page")
    document = {"nodes": {"start": {"step": "echo"}}}
    assert Definition.check(document, subject_model="knowledge.page")[1] == []
    for subject_model in ("", "knowledge.vault", "knowledge.Page"):
        issues = Definition.check(document, subject_model=subject_model)[1]
        assert any(issue.code == "subject" and not issue.blocks_draft for issue in issues)


@pytest.mark.django_db(transaction=True)
def test_publish_canonicalizes_mixed_case_step_subject(execution, register_step):
    """A declared model name publishes against the same canonical workflow subject."""
    actor, _ = execution

    class PageStep(Echo):
        """A step naming its concrete subject with Django's model class spelling."""

        key = "page_step"
        subject = "knowledge.Page"

    register_step(PageStep)
    workflow = Workflow.objects.install_definition(
        key="subject_case",
        name="Subject case",
        subject_model="knowledge.Page",
        draft={"nodes": {"start": {"step": PageStep.key}}},
        actor=actor,
    )
    assert workflow.published_id is not None
    assert workflow.subject_model == PageStep.subject == "knowledge.page"


@pytest.mark.parametrize("subject", ["unknown.Record", "knowledge.Missing", "malformed", ""])
def test_unknown_step_subject_is_a_resolution_and_publish_error(register_step, subject):
    """Invalid subject labels fail at the registry owner and surface as definition issues."""

    class UnknownSubject(Echo):
        """A step whose declared subject cannot resolve to an installed model."""

        key = "unknown_subject"

    UnknownSubject.subject = subject
    register_step(UnknownSubject)
    with pytest.raises(ImproperlyConfigured):
        resolve_step(UnknownSubject.key)
    definition, issues = Definition.check({"nodes": {"start": {"step": UnknownSubject.key}}})
    assert definition is not None
    assert any(issue.code == "unknown_step" and issue.node == "start" for issue in issues)


def test_publish_reports_config_edges_cycles_and_unreachable_nodes():
    """Publish reports config edges cycles and unreachable nodes."""
    definition, issues = Definition.check(
        {
            "nodes": {
                "start": {"step": "echo", "next": {"missing": "absent"}},
                "loop_a": {"step": "echo", "next": {"done": "loop_b"}},
                "loop_b": {"step": "echo", "next": {"done": "loop_a"}},
                "bad_config": {"step": "configured", "config": {"amount": "invalid"}},
            }
        }
    )
    assert definition is not None
    assert {issue.code for issue in issues} >= {"outcome", "target", "cycle", "config", "entry"}
    disconnected = graph({"start": {}, "loop_a": {"next": {"done": "loop_b"}}, "loop_b": {"next": {"done": "loop_a"}}})
    assert {issue.node for issue in disconnected.issues() if issue.code == "unreachable"} == {"loop_a", "loop_b"}


def test_binding_literals_paths_fallbacks_and_skipped_sources():
    """Binding literals paths fallbacks and skipped sources."""
    definition = graph(
        {
            "start": {"next": {"done": "left", "alternate": "right"}},
            "left": {"next": {"done": "finish"}},
            "right": {"next": {"done": "finish"}},
            "finish": {"input": {"value": {"from": ["right", "left"], "path": ["value"]}}},
        }
    )
    assert definition.issues() == []
    rows = [row("start"), row("left", output={"value": 9}), row("right", "skipped")]
    assert definition.input_for("finish", {}, rows) == {"value": 9}
    constant = graph({"start": {"input": {"value": {"value": 11}}}})
    assert constant.validate_input({}) == {}
    assert constant.input_for("start", {}, []) == {"value": 11}


def test_binding_schema_mismatch_is_publish_issue():
    """Binding schema mismatch is publish issue."""
    definition = graph(
        {
            "start": {"next": {"done": "finish"}},
            "finish": {"step": "text_echo", "input": {"value": {"from": "start", "path": ["value"]}}},
        }
    )
    assert any(issue.code == "binding" for issue in definition.issues())
    literal = graph({"start": {"input": {"value": {"value": "wrong"}}}})
    assert any(issue.code == "binding" for issue in literal.issues())


def test_incompatible_default_input_is_rejected_before_publication(monkeypatch):
    """Incompatible default input is rejected before publication."""
    definition = graph({"start": {"next": {"done": "finish"}}, "finish": {"step": "text_echo"}})
    assert any(issue.node == "finish" and issue.code == "input" for issue in definition.issues())
    monkeypatch.setattr(TextEcho, "input_model", None)
    assert definition.issues() == []


def test_error_default_input_requires_defaults_or_explicit_bindings(monkeypatch):
    """Error default input requires defaults or explicit bindings."""
    definition = graph({"start": {"next": {"error": "finish"}}, "finish": {}})
    assert any(issue.code == "input" and "error edge" in issue.message for issue in definition.issues())

    class DefaultPayload(BaseModel):
        """DefaultPayload."""

        value: int = 0

    monkeypatch.setattr(Echo, "input_model", DefaultPayload)
    assert definition.issues() == []
    bound = definition.input_for("finish", {}, [row("start", "failed", "error")])
    assert bound == {}
    assert Echo.parse_input(bound).value == 0


def test_missing_required_binding_is_publish_issue():
    """Missing required binding is publish issue."""
    definition = graph({"start": {"input": {}}})
    assert any(issue.code == "binding" and issue.path[-1] == "value" for issue in definition.issues())


def test_whole_object_binding_and_projection_use_target_contract():
    """Whole object binding and projection use target contract."""
    definition = graph({"start": {"next": {"done": "finish"}}, "finish": {"input": {"from": "start", "project": True}}})
    assert definition.issues() == []
    assert definition.input_for("finish", {}, [row("start", output={"value": 2, "extra": True})]) == {"value": 2}


def test_binding_from_non_ancestor_is_publish_issue():
    """Binding from non ancestor is publish issue."""
    definition = graph(
        {
            "start": {"next": {"done": ["left", "right"]}},
            "left": {"input": {"value": {"from": "right", "path": ["value"]}}},
            "right": {},
        }
    )
    assert any("ancestor" in issue.message for issue in definition.issues())


def test_unbound_join_requires_explicit_input_even_when_schemas_match():
    """Unbound join requires explicit input even when schemas match."""
    definition = graph(
        {
            "start": {"next": {"done": ["left", "right"]}},
            "left": {"next": {"done": "finish"}},
            "right": {"next": {"done": "finish"}},
            "finish": {},
        }
    )
    assert any(issue.node == "finish" and issue.code == "input" for issue in definition.issues())
    definition.nodes["right"].step = "text_echo"
    assert any(issue.node == "finish" and issue.code == "input" for issue in definition.issues())


def test_workflow_input_includes_fields_bound_by_later_nodes():
    """Workflow input includes fields bound by later nodes."""
    definition = graph(
        {"start": {"next": {"done": "finish"}}, "finish": {"input": {"value": {"from": "input", "path": ["later"]}}}}
    )
    assert definition.validate_input({"value": 1, "later": 7}) == {"value": 1, "later": 7}
    assert definition.input_for("start", {"value": 1, "later": 7}, []) == {"value": 1}
    assert definition.input_for("finish", {"value": 1, "later": 7}, [row("start")]) == {"value": 7}
    with pytest.raises(ValidationError, match="required"):
        definition.validate_input({"value": 1})
    with pytest.raises(ValidationError):
        definition.validate_input({"value": 1, "later": "bad"})


def test_results_choose_first_eligible_then_rename_and_project():
    """Results choose first eligible then rename and project."""
    definition = graph(
        {"start": {}},
        [
            {"from": "start", "when": ["alternate"]},
            {"from": "start", "as": "reported", "output": {"copied": {"from": "start", "path": ["value"]}}},
            {"from": "start", "as": "later"},
        ],
    )
    assert definition.issues() == []
    assert definition.result_for([row("start", output={"value": 5})], {}) == ("reported", {"copied": 5})
    assert definition.result_for([row("start", "skipped")], {}) is None


@pytest.mark.parametrize(
    "when,outcome,expected",
    [(None, "done", True), (None, "alternate", True), (None, ERROR_OUTCOME, False),
     ([ERROR_OUTCOME], ERROR_OUTCOME, True), ([], "done", False)],
)
def test_result_eligibility_requires_explicit_error_selection(when, outcome, expected):
    """Default results omit failed producers while explicit error results remain usable."""
    declaration = {"from": "start", **({"when": when} if when is not None else {})}
    definition = graph({"start": {}}, [declaration])
    assert definition.issues() == []
    producer = row("start", "failed" if outcome == ERROR_OUTCOME else "succeeded", outcome)
    assert definition.result_for([producer], {}) == ((outcome, producer.output) if expected else None)


def test_recovery_result_wins_after_failed_default_producer():
    """An earlier unqualified failed producer cannot report error after recovery."""
    definition = graph(
        {"start": {"next": {ERROR_OUTCOME: "recover"}}, "recover": {"input": {"value": {"value": 0}}}},
        [{"from": "start"}, {"from": "recover"}],
    )
    rows = [row("start", "failed", ERROR_OUTCOME), row("recover")]
    assert not definition.unrouted_failure(rows)
    assert definition.result_for(rows, {}) == ("done", {"value": 1})
    assert graph({"start": {}}).unrouted_failure(rows[:1])
    assert not graph({"start": {}}).unrouted_failure([row("start")])


@pytest.mark.parametrize("source", ["start", "input"])
def test_whole_result_binding_rejects_error_eligibility(source):
    """An explicit error result may report its producer output only without a whole binding."""
    definition = graph(
        {"start": {}}, [{"from": "start", "when": [ERROR_OUTCOME], "output": {"from": source}}]
    )
    assert any(issue.path == ["results", 0, "output"] and "error outcome" in issue.message
               for issue in definition.issues())


@pytest.mark.parametrize(
    "path,valid",
    [([], True), (["required", "value"], True), (["required", "optional"], False),
     (["optional", "value"], False), (["items", 0, "value"], True), (["items", 1, "value"], False)],
)
@pytest.mark.parametrize("source", ["start", "input"])
def test_whole_result_path_must_be_required_at_every_level(monkeypatch, source, path, valid):
    """Required parents, leaves and array bounds are proven through local model refs."""
    class Nested(BaseModel):
        """A payload with a required field and an omittable field."""

        value: int
        optional: int = 0

    class Structured(BaseModel):
        """A source declaring both mandatory and optional nested paths."""

        required: Nested
        optional: Nested = Field(default_factory=lambda: Nested(value=0))
        items: list[Nested] = Field(min_length=1)

    monkeypatch.setattr(Echo, "input_model", Structured)
    monkeypatch.setattr(Echo, "output_model", Structured)
    definition = graph({"start": {}}, [{"from": "start", "output": {"from": source, "path": path}}])
    issues = definition.issues()
    if valid:
        assert issues == []
    else:
        assert any(issue.path == ["results", 0, "output"] and "required at every level" in issue.message
                   for issue in issues)


def test_whole_result_can_read_required_composed_input_path():
    """Later required input consumers establish guarantees for result bindings too."""
    definition = graph(
        {"start": {"next": {"done": "finish"}},
         "finish": {"input": {"value": {"from": "input", "path": ["later", 1]}}}},
        [{"from": "finish", "output": {"from": "input", "path": ["later", 1]}}],
    )
    assert definition.issues() == []
    assert definition.result_for([row("start"), row("finish")], {"value": 1, "later": [0, 7]}) == ("done", 7)


def test_whole_result_binding_rejects_other_branch_at_publication():
    """Whole result binding rejects other branch at publication."""
    definition = graph(
        {"start": {"next": {"done": "left", "alternate": "right"}}, "left": {}, "right": {}},
        [{"from": "left", "output": {"from": "right"}}],
    )
    assert any(issue.code == "binding" and issue.path == ["results", 0, "output"] for issue in definition.issues())


def test_generics_set_existing_config_owner_and_normalize_models():
    """Generics set existing config owner and normalize models."""
    assert issubclass(Configured, ImplBase)
    assert Configured.config_model is Configuration
    assert Configured.config_defaults() == {"amount": 3}
    assert Configured.config({}).amount == 3
    assert Configured.normalize_input({"value": "2"}) == {"value": 2}
    output = Payload(value=8)
    completion = Configured.done(output)
    assert completion.output is output
    assert Configured.check(completion).output == {"value": 8}
    with pytest.raises(ValidationError) as error:
        Configured.check(Configured.done({"value": "invalid"}))
    assert "output.value" in error.value.message_dict
    with pytest.raises(ValidationError, match="outcome"):
        Configured.check(Configured.done(Payload(value=8), outcome="missing"))
    with pytest.raises(ValidationError, match="does not accept configuration"):
        Echo.config({"unused": True})


def test_config_parser_applies_each_validator_once(monkeypatch):
    """Config parser applies each validator once."""
    seen = []

    class IncrementedConfiguration(BaseModel):
        """IncrementedConfiguration."""

        amount: int

        @field_validator("amount")
        @classmethod
        def increment(cls, value):
            """Increment."""
            seen.append(value)
            return value + 1

    monkeypatch.setattr(Configured, "config_model", IncrementedConfiguration)
    config = Configured.config({"amount": 2})
    assert config.amount == 3
    assert seen == [2]
    seen.clear()
    definition = graph({"start": {"step": "configured", "config": {"amount": 2}}}, [{"from": "start"}])
    assert definition.issues() == []
    assert seen == [2]


def test_dynamic_outcome_hook_retains_builtin_error_routing():
    """Dynamic outcome hook retains builtin error routing."""
    definition = graph(
        {
            "start": {"step": "dynamic", "config": {"amount": 1}, "next": {"added": "finish", "error": "recover"}},
            "finish": {},
            "recover": {"input": {"value": {"value": 0}}},
        }
    )
    assert definition.issues() == []
    config = Dynamic.config({"amount": 1})
    assert Dynamic.available_outcomes(config) == {"added": "Added", "error": "Error"}
    assert Dynamic.check(Dynamic.done(Payload(value=1), outcome="added"), config=config).outcome == "added"
    with pytest.raises(ValidationError, match="outcome"):
        Dynamic.check(Dynamic.done(Payload(value=1), outcome="zero"), config=config)


def test_wait_requires_aware_deadline():
    """Time waits reject deadlines without a timezone."""
    with pytest.raises(ValueError, match="aware"):
        Wait(until=datetime(2026, 1, 1))
    assert Wait(until=datetime(2026, 1, 1, tzinfo=timezone.utc)).kind == "wait"


def test_resolve_step_rejects_timeouts_that_would_disable_postgresql_limit(monkeypatch):
    """Resolve step rejects timeouts that would disable postgresql limit."""
    monkeypatch.setattr(Echo, "timeout", timedelta(microseconds=1))
    with pytest.raises(ImproperlyConfigured, match="at least 1 millisecond"):
        resolve_step("echo")
    monkeypatch.setattr(Echo, "timeout", timedelta(milliseconds=1))
    assert resolve_step("echo") is Echo


def test_source_fallback_ignores_failed_sources():
    """A failed earlier source cannot displace a succeeded fallback."""
    definition = graph(
        {
            "start": {"next": {"done": ["left", "right"]}},
            "left": {"next": {"error": "finish"}},
            "right": {"next": {"done": "finish"}},
            "finish": {"input": {"value": {"from": ["left", "right"], "path": ["value"]}}},
        }
    )
    assert definition.issues() == []
    assert definition.input_for(
        "finish", {}, [row("start"), row("left", "failed", "error"), row("right", output={"value": 8})]
    ) == {"value": 8}


def test_result_field_sources_are_limited_to_producer_and_ancestors():
    """Result projections may omit unavailable ancestors but cannot read siblings."""
    definition = graph(
        {
            "start": {"next": {"done": ["left", "right"]}},
            "left": {},
            "right": {},
        },
        [{"from": "left", "output": {"value": {"from": "right", "path": ["value"]}}}],
    )
    assert any(issue.code == "binding" for issue in definition.issues())
    definition.results[0].output = {"value": SourceBinding.model_validate({"from": "start", "path": ["value"]})}
    assert definition.issues() == []
    assert definition.result_for([row("start", "skipped"), row("left")], {}) == ("done", {})


def test_whole_result_allows_input_and_producer_but_not_ancestor():
    """Whole results have a narrower allowed source set than field projections."""
    definition = graph(
        {"start": {"next": {"done": "finish"}}, "finish": {}}, [{"from": "finish", "output": {"from": "input"}}]
    )
    assert definition.issues() == []
    assert definition.result_for([row("start"), row("finish")], {"value": 7}) == ("done", {"value": 7})
    definition.results[0].output = SourceBinding.model_validate({"from": "start"})
    assert any(issue.code == "binding" for issue in definition.issues())


def test_done_cannot_forge_the_builtin_error_outcome():
    """Only failure settlement owns the error outcome."""
    with pytest.raises(ValidationError, match="success outcome"):
        Echo.check(Echo.done({"value": 1}, outcome="error"))


def test_config_and_input_errors_keep_inherited_field_paths():
    """All typed boundaries expose Django validation errors, including nested fields."""
    for parse, value, path in (
        (Configured.config, {"amount": "bad"}, "config.amount"),
        (Configured.parse_input, {"value": "bad"}, "input.value"),
    ):
        with pytest.raises(ValidationError) as error:
            parse(value)
        assert path in error.value.message_dict


def test_derived_input_schema_includes_later_binding_fields():
    """The public schema and admission agree on extended workflow input."""
    definition = graph(
        {"start": {"next": {"done": "finish"}}, "finish": {"input": {"value": {"from": "input", "path": ["later"]}}}}
    )
    validator = Draft202012Validator(definition.input_schema)
    assert validator.is_valid({"value": 1, "later": 2})
    assert not validator.is_valid({"value": 1})
    assert not validator.is_valid({"value": 1, "later": "invalid"})


def test_whole_input_projection_extends_run_input_and_limits_entry_fields(monkeypatch):
    """A later whole projection contributes its own fields to the run input."""

    class LaterPayload(BaseModel):
        """LaterPayload."""

        model_config = ConfigDict(extra="forbid")
        later: int

    monkeypatch.setattr(TextEcho, "input_model", LaterPayload)
    definition = graph(
        {
            "start": {"next": {"done": "finish"}},
            "finish": {"step": "text_echo", "input": {"from": "input", "project": True}},
        }
    )
    assert definition.issues() == []
    assert definition.validate_input({"value": 1, "later": 2}) == {"value": 1, "later": 2}
    assert definition.input_for("start", {"value": 1, "later": 2}, []) == {"value": 1}
    assert definition.input_for("finish", {"value": 1, "later": 2}, [row("start")]) == {"later": 2}


def test_result_schema_describes_projection_instead_of_producer():
    """Projected result schemas carry literal and renamed fields, not producer fields."""
    definition = graph(
        {"start": {}},
        [
            {
                "from": "start",
                "output": {
                    "status": {"value": "finished"},
                    "copied": {"from": "start", "path": ["value"]},
                },
            }
        ],
    )
    validator = Draft202012Validator(definition.result_schema(definition.results[0]))
    assert validator.is_valid({"status": "finished", "copied": 4})
    assert validator.is_valid({"status": "finished"})
    assert not validator.is_valid({"value": 4})
    assert not validator.is_valid({"status": "finished", "copied": "bad"})


def test_conflicting_input_consumers_are_a_publish_issue():
    """Different types for the same input path cannot produce a valid contract."""
    definition = graph(
        {
            "start": {"next": {"done": "finish"}},
            "finish": {"step": "text_echo", "input": {"value": {"from": "input", "path": ["value"]}}},
        }
    )
    assert any(issue.code == "binding" and "incompatible" in issue.message for issue in definition.issues())


def test_composed_schemas_keep_nested_model_references_local(monkeypatch):
    """Nested definitions stay scoped when projected into a result field."""

    class Nested(BaseModel):
        """A nested output contract with a generated schema reference."""

        value: int

    class Structured(BaseModel):
        """A result producer whose payload is described through a local ref."""

        payload: Nested

    monkeypatch.setattr(Echo, "output_model", Structured)
    definition = graph(
        {"start": {}},
        [
            {
                "from": "start",
                "output": {
                    "copied": {"from": "start", "path": ["payload"]},
                },
            }
        ],
    )
    assert definition.issues() == []
    validator = Draft202012Validator(definition.result_schema(definition.results[0]))
    assert validator.is_valid({"copied": {"value": 3}})
    assert not validator.is_valid({"copied": {"value": "bad"}})


def test_nested_input_paths_are_derived_without_losing_target_constraints():
    """Object and positional-array paths preserve the bound field's type."""
    definition = graph(
        {"start": {"next": {"done": "finish"}}, "finish": {"input": {"value": {"from": "input", "path": ["later", 1]}}}}
    )
    assert definition.issues() == []
    assert definition.validate_input({"value": 1, "later": ["ignored", 3]}) == {
        "value": 1,
        "later": ["ignored", 3],
    }
    validator = Draft202012Validator(definition.input_schema)
    assert not validator.is_valid({"value": 1, "later": [0]})
    assert not validator.is_valid({"value": 1, "later": [0, "invalid"]})


def test_closed_nested_entry_extension_is_rejected_before_publication(monkeypatch):
    """Publication must not advertise input that the entry's nested model rejects."""

    class Context(BaseModel):
        """An entry-owned closed object."""

        model_config = ConfigDict(extra="forbid")
        known: int

    class NestedInput(BaseModel):
        """An entry contract that cannot receive nested extension fields."""

        context: Context

    monkeypatch.setattr(Configured, "input_model", NestedInput)
    definition = graph(
        {
            "start": {"step": "configured", "next": {"done": "finish"}},
            "finish": {"input": {"value": {"from": "input", "path": ["context", "extra"]}}},
        }
    )
    assert any(issue.code == "binding" and "closed nested" in issue.message for issue in definition.issues())


@pytest.mark.parametrize("outcome", ["x" * 64, "Uppercase", "has space"])
@pytest.mark.parametrize("declaration", ["next", "when", "as"])
def test_invalid_authored_outcomes_are_parse_issues(outcome, declaration):
    """Edges, result selections and aliases share the node-key declaration syntax."""
    document = {"nodes": {"start": {"step": "echo"}}, "results": [{"from": "start"}]}
    if declaration == "next":
        document["nodes"]["start"]["next"] = {outcome: "finish"}
        document["nodes"]["finish"] = {"step": "echo"}
        path = ["nodes", "start", "next", outcome, "[key]"]
    else:
        document["results"][0][declaration] = [outcome] if declaration == "when" else outcome
        path = ["results", 0, declaration, *([0] if declaration == "when" else [])]
    definition, issues = Definition.check(document)
    assert definition is None
    assert any(issue.code == "parse" and issue.path == path and issue.blocks_draft for issue in issues)


@pytest.mark.parametrize("outcome", ["x" * 64, "Uppercase", "has space"])
def test_invalid_static_outcomes_fail_at_class_declaration(outcome):
    """A static step cannot register an invalid outcome declaration."""
    with pytest.raises(ValidationError, match="outcomes"):

        class InvalidOutcome(Echo):
            """An invalid static declaration caught before publication."""

            key = "invalid_outcome"
            outcomes = {outcome: "Unusable"}


@pytest.mark.parametrize("outcome", ["x" * 64, "Uppercase", "has space"])
def test_invalid_dynamic_outcomes_are_publish_issues(monkeypatch, outcome):
    """Config-derived names are checked even when no edge or result names them."""
    monkeypatch.setattr(Dynamic, "outcomes_for", classmethod(lambda cls, config: {outcome: "Unusable"}))
    definition = graph({"start": {"step": "dynamic"}})
    assert any(issue.code == "outcome" and issue.node == "start" for issue in definition.issues())


def test_outcomes_accept_the_shared_storage_boundary(register_step):
    """A 63-character name is valid as a node, edge, result selection and alias."""
    outcome = "x" * 63

    class BoundaryOutcome(Echo):
        """A static outcome at the shared declaration limit."""

        key = "boundary_outcome"
        outcomes = {outcome: "Boundary"}

    register_step(BoundaryOutcome)
    definition = graph(
        {outcome: {"step": "boundary_outcome", "next": {outcome: "finish"}}, "finish": {}},
        [{"from": outcome, "when": [outcome], "as": outcome}],
    )
    assert definition.issues() == []
    assert BoundaryOutcome.done({"value": 1}, outcome=outcome).outcome == outcome


def test_whole_null_binding_is_rejected_but_nested_null_is_preserved(monkeypatch):
    """Root input null cannot enter a nonnull JSON column; object fields may be null."""
    monkeypatch.setattr(Echo, "input_model", None)
    definition = graph({"start": {"input": {"from": "input", "path": ["optional"]}}})
    assert definition.issues() == []
    with pytest.raises(ValidationError, match="whole step input cannot be null"):
        definition.validate_input({"optional": None})
    with pytest.raises(ValidationError, match="whole step input cannot be null"):
        definition.input_for("start", {"optional": None}, [])
    assert definition.validate_input({"optional": {"value": None}}) == {"optional": {"value": None}}
    assert definition.input_for("start", {"optional": {"value": None}}, []) == {"value": None}
