"""Outcome-keyed run contracts and the configured await declaration boundary."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict

from angee.base.identity import public_id_of
from angee.base.jsonschema import schemas_match, validator
from angee.base.scoping import system_queryset
from angee.workflows.awaits import AwaitedRun, AwaitRun, AwaitRunConfig, AwaitRunInput
from angee.workflows.definition import Definition
from angee.workflows.steps import Done, EmptyOutput, Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, Workflow, WorkflowVersion


class Number(BaseModel):
    """A strict producer and successor value."""

    model_config = ConfigDict(extra="forbid")
    value: int


class NumberStep(Step[Number, Number, None]):
    """Offer two named outcomes sharing one data shape."""

    key = "await_number"
    outcomes = {"positive": "Positive", "negative": "Negative"}


class EmptyStep(Step[EmptyOutput, EmptyOutput, None]):
    """Consume an empty built-in terminal outcome."""

    key = "await_empty"


@pytest.fixture(autouse=True)
def steps(register_step):
    """Registry changes are restored by the shared fixture for every worker."""
    for step in (AwaitRun, NumberStep, EmptyStep):
        register_step(step)


def child_document():
    """A child advertises different projected outputs for its two outcomes."""
    return {
        "nodes": {"value": {"step": NumberStep.key}},
        "results": [
            {"from": "value", "when": ["positive"], "as": "accepted"},
            {"from": "value", "when": ["negative"], "as": "declined", "output": {"reason": {"value": "negative"}}},
        ],
    }


def parent_document(expects):
    """Route each child result to a successor with exactly its typed contract."""
    return {"nodes": {
        "awaited": {"step": AwaitRun.key, "config": {"expects": expects}, "next": {
            "accepted": "value", "declined": "empty", "done": "empty", "error": "empty", "canceled": "empty",
        }},
        "value": {"step": NumberStep.key},
        "empty": {"step": EmptyStep.key, "input": {"from": "input", "path": ["empty"]}},
    }}


def test_run_output_schemas_are_keyed_by_effective_result_outcome():
    """Projected aliases retain their actual schema, plus terminal empty defaults."""
    definition = Definition.model_validate(child_document())
    schemas = definition.output_schemas
    assert set(schemas) == {"accepted", "declined", "done", "error", "canceled"}
    assert schemas_match(schemas["accepted"], NumberStep.output_schema())
    assert validator(schemas["declined"]).is_valid({"reason": "negative"})
    assert not validator(schemas["declined"]).is_valid({"value": 1})
    for outcome in ("done", "error", "canceled"):
        assert validator(schemas[outcome]).is_valid({})
        assert not validator(schemas[outcome]).is_valid({"value": 1})


def test_shared_result_outcome_combines_projected_alternatives():
    """Two producers sharing an outcome contribute their individual projections."""
    document = child_document()
    document["results"][0]["as"] = "shared"
    document["results"][1]["as"] = "shared"
    schema = Definition.model_validate(document).output_schemas["shared"]
    assert "anyOf" in schema
    contract = validator(schema)
    assert contract.is_valid({"value": 2})
    assert contract.is_valid({"reason": "negative"})
    assert not contract.is_valid({})


def test_result_without_when_excludes_error_from_its_output_contract():
    """The same eligibility rule drives runtime results and declared output."""
    document = child_document()
    document["results"] = [{"from": "value"}]
    schemas = Definition.model_validate(document).output_schemas
    assert set(schemas) == {"positive", "negative", "done", "error", "canceled"}
    assert not validator(schemas["error"]).is_valid({"value": 3})


@pytest.mark.parametrize("outcome", ["done", "error", "canceled"])
def test_declared_result_coexists_with_native_empty_outcome_schema(outcome):
    """An authored projection cannot hide failure, cancellation or no-result output."""
    document = child_document()
    document["results"] = [{"from": "value", "when": ["positive"], "as": outcome}]
    schema = Definition.model_validate(document).output_schemas[outcome]
    assert validator(schema).is_valid({"value": 2})
    assert validator(schema).is_valid({})


def test_equal_result_contracts_do_not_add_duplicate_union_branches():
    """The native empty schema is reused when a result reports the same value."""
    document = {"nodes": {"empty": {"step": EmptyStep.key}}, "results": [{"from": "empty"}]}
    schema = Definition.model_validate(document).output_schemas["done"]
    assert schemas_match(schema, EmptyStep.output_schema())
    assert "anyOf" not in schema


def test_native_output_alternatives_preserve_nested_model_references(register_step):
    """Adding native fallbacks retains each model's root-local definition scope."""
    class Envelope(BaseModel):
        payload: Number

    class Nested(Step[None, Envelope, None]):
        key = "await_nested"

    register_step(Nested)
    definition = Definition.model_validate({"nodes": {"nested": {"step": Nested.key}},
                                           "results": [{"from": "nested"}]})
    contract = validator(definition.output_schemas["done"])
    assert contract.is_valid({})
    assert contract.is_valid({"payload": {"value": 3}})
    assert not contract.is_valid({"payload": {"value": "invalid"}})


@pytest.mark.django_db(transaction=True)
def test_await_requires_published_expected_workflow(execution):
    """Absent and unpublished identities are publication issues, not parse errors."""
    actor, _ = execution
    _, issues = Definition.check(parent_document("missing"))
    assert any(issue.code == "outcome" and "published" in issue.message for issue in issues)
    Workflow.objects.install_definition(
        key="unpublished", name="Unpublished", draft=child_document(), publish=False, actor=actor,
    )
    _, issues = Definition.check(parent_document("unpublished"))
    assert any(issue.code == "outcome" and "published" in issue.message for issue in issues)


@pytest.mark.django_db(transaction=True)
def test_await_requires_every_outcome_route_and_checks_successor_by_outcome(execution):
    """An accepted edge supplies Number while default failures remain empty."""
    actor, _ = execution
    workflow = load_workflow(child_document(), key="await_contract", actor=actor)
    document = parent_document(workflow.key)
    definition, issues = Definition.check(document)
    assert issues == []
    assert schemas_match(definition.output_schema("awaited", {"accepted"}), NumberStep.input_schema())
    del document["nodes"]["awaited"]["next"]["canceled"]
    _, issues = Definition.check(document)
    assert any(issue.code == "required_outcome" and issue.path[-1] == "canceled" for issue in issues)
    document["nodes"]["awaited"]["next"]["canceled"] = "value"
    _, issues = Definition.check(document)
    assert any(issue.code == "input" and issue.node == "value" for issue in issues)


@pytest.mark.django_db(transaction=True)
def test_await_checks_output_against_the_selected_child_outcome(execution):
    """An await can forward error, but cannot forward another outcome's shape."""
    actor, _ = execution
    workflow = load_workflow(child_document(), key="await_checks", actor=actor)
    config = AwaitRunConfig(expects=workflow.key)
    assert AwaitRun.check(Done(output={"value": 2}, outcome="accepted"), config=config).output == {"value": 2}
    assert AwaitRun.check(Done(output={}, outcome="error"), config=config).outcome == "error"
    assert AwaitRun.check(Done(output={}, outcome="canceled"), config=config).outcome == "canceled"
    with pytest.raises(ValidationError):
        AwaitRun.check(Done(output={"value": 2}, outcome="declined"), config=config)
    with pytest.raises(ValidationError, match="does not offer"):
        AwaitRun.check(Done(output={}, outcome="unknown"), config=config)
    wait = AwaitedRun(run_id=123)
    assert AwaitRun.check(wait, config=config) is wait
    assert wait.wait_parameters() == {"kind": "run"}
    done = AwaitedRun(run_id=123, kind="done", outcome="accepted", output={"value": 3})
    checked = AwaitRun.check(done, config=config)
    assert isinstance(checked, AwaitedRun) and checked.run_id == 123 and checked.wait_parameters() is None


@pytest.mark.django_db(transaction=True)
def test_await_successor_field_binding_uses_its_routed_outcome_contract(execution):
    """An accepted edge can bind accepted fields without admitting empty closures."""
    actor, _ = execution
    workflow = load_workflow(child_document(), key="await_field_binding", actor=actor)
    document = parent_document(workflow.key)
    document["nodes"]["value"]["input"] = {"value": {"from": "awaited", "path": ["value"]}}
    _, issues = Definition.check(document)
    assert issues == []
    document["nodes"]["awaited"]["next"]["declined"] = "value"
    _, issues = Definition.check(document)
    assert any(issue.code == "binding" and issue.node == "value" for issue in issues)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("whole", [False, True])
def test_await_result_projection_uses_selected_outcome_contract(execution, whole):
    """A result's eligibility selects the same child shape as its binding."""
    actor, _ = execution
    workflow = load_workflow(child_document(), key="await_result_binding", actor=actor)
    document = parent_document(workflow.key)
    binding = {"from": "awaited", "path": ["value"]}
    document["results"] = [{"from": "awaited", "when": ["accepted"], "as": "answer",
                            "output": binding if whole else {"answer": binding}}]
    definition, issues = Definition.check(document)
    assert issues == []
    assert validator(definition.output_schemas["answer"]).is_valid(3 if whole else {"answer": 3})


@pytest.mark.django_db(transaction=True)
def test_await_whole_error_result_retains_the_publication_restriction(execution):
    """Dynamic empty outcomes do not remove the graph's universal error-binding rule."""
    actor, _ = execution
    workflow = load_workflow(child_document(), key="await_error_binding", actor=actor)
    document = parent_document(workflow.key)
    document["results"] = [{"from": "awaited", "when": ["error"], "output": {"from": "awaited"}}]
    _, issues = Definition.check(document)
    assert any(issue.code == "binding" and "error" in issue.message for issue in issues)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failed", [False, True])
def test_await_map_body_preserves_error_label_and_child_output(execution, register_step, failed):
    """Observing a child's failure succeeds; its error label is still its output contract."""
    class ProduceNumber(NumberStep):
        def run(self, ctx):
            return ctx.fail("Unavailable.") if failed else ctx.done(ctx.input, outcome="positive")

    register_step(ProduceNumber)
    actor, _ = execution
    document = child_document()
    document["results"][0]["as"] = "error"
    workflow = load_workflow(document, key="await_observed_error", actor=actor)
    child = start_run(workflow, actor=actor, input={"value": 3})
    run_until(child)
    assert child.outcome == "error"
    parent = load_workflow({
        "nodes": {"each": {"step": "map", "body": {
            "step": AwaitRun.key, "config": {"expects": workflow.key},
        }}}, "results": [{"from": "each"}],
    }, key="await_observer_map", actor=actor)
    run = start_run(parent, actor=actor, input={"items": [{"run_id": public_id_of(child)}]})
    run_until(run)
    assert run.status == "succeeded", run.error
    assert run.output == [{"index": 0, "outcome": "error", "output": {} if failed else {"value": 3}}]
    assert system_queryset(StepRun).get(run=run, node_key="each.body").awaited_run_id == child.pk


@pytest.mark.django_db(transaction=True)
def test_observing_already_failed_owned_child_preserves_its_open_rows(execution, register_step):
    """Immediate terminal observation counts as awaited when the parent finishes."""
    class Branch(Step[None, None, None]):
        key = "await_branch"

        def run(self, ctx):
            return ctx.done()

    class Park(Branch):
        key = "await_park"

        def run(self, ctx):
            return ctx.wait(until=ctx.now + timedelta(days=1))

    class Fail(Branch):
        key = "await_fail"

        def run(self, ctx):
            return ctx.fail("Unavailable.")

    for step in (Branch, Park, Fail):
        register_step(step)
    actor, _ = execution
    child_workflow = load_workflow({"nodes": {
        "branch": {"step": Branch.key, "next": {"done": ["park", "fail"]}},
        "park": {"step": Park.key}, "fail": {"step": Fail.key},
    }}, key="await_failed_child", actor=actor)
    admitted = []

    class StartOwned(Step[None, AwaitRunInput, None]):
        key = "await_start_owned"

        def run(self, ctx):
            child = ctx.start_run(child_workflow, relation="owned")
            admitted.append(child)
            return ctx.done({"run_id": public_id_of(child)})

    register_step(StartOwned)
    parent_workflow = load_workflow({"nodes": {
        "start": {"step": StartOwned.key, "next": {"done": "await"}},
        "await": {"step": AwaitRun.key, "config": {"expects": child_workflow.key},
                  "next": {outcome: "finish" for outcome in ("done", "error", "canceled")}},
        "finish": {"step": Branch.key},
    }}, key="await_failed_parent", actor=actor)
    parent = start_run(parent_workflow, actor=actor)
    StepRun.objects.execute(system_queryset(StepRun).get(run=parent, node_key="start").pk)
    child = admitted[0]
    for key in ("branch", "park", "fail"):
        StepRun.objects.execute(system_queryset(StepRun).get(run=child, node_key=key).pk)
    child.refresh_from_db()
    assert child.status == "failed"
    parked = system_queryset(StepRun).get(run=child, node_key="park")
    assert parked.status == "waiting"
    run_until(parent)
    assert parent.status == "succeeded"
    observed = system_queryset(StepRun).get(run=parent, node_key="await")
    assert observed.attempt == 1 and observed.awaited_run_id == child.pk
    parked.refresh_from_db()
    assert parked.status == "waiting"


@pytest.mark.django_db(transaction=True)
def test_recursive_await_contract_is_a_publish_issue_and_restores_guard(execution):
    """Old published cycles fail as diagnostics instead of unbounded recursion."""
    actor, _ = execution
    workflow = load_workflow(child_document(), key="await_recursive", actor=actor)
    # Seed a historical malformed publication through the model's insertion door.
    document = parent_document(workflow.key)
    document["results"] = [{"from": "awaited", "when": ["accepted"]}]
    version = system_queryset(WorkflowVersion).create(
        workflow=workflow, number=2, document=document, content_hash="recursive",
    )
    system_queryset(Workflow).filter(pk=workflow.pk).update(published=version)
    _, issues = Definition.check(parent_document(workflow.key))
    assert any("cannot be recursive" in issue.message for issue in issues)
    unrelated = load_workflow(child_document(), key="await_unrelated", actor=actor)
    assert "accepted" in AwaitRun.outcomes_for(AwaitRunConfig(expects=unrelated.key))
