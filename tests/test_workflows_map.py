"""Map bodies use the ordinary planner, settlements, recovery and read resources."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.identity import public_id_of
from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.events import ChangeRelatedRecord
from angee.workflows import schema as workflow_schema
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.maps import MapItem
from angee.workflows.runner import runner
from angee.workflows.steps import Retryable, RetryPolicy, Step, StepMode
from angee.workflows.testing.drivers import decide, load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRun
from tests.conftest import addon_schema, create_user, execute_schema, result_data, vault_for


class MapEcho(Step[None, None, None]):
    """Return each item's JSON through the same contract as a normal body."""

    key = "map_echo"

    def run(self, ctx):
        return ctx.done(ctx.input)


@pytest.fixture
def map_steps(register_step):
    """Restore the registered fixture body after each test and worker."""
    register_step(MapEcho)


def map_document(body=MapEcho.key, *, routed=False, body_input=None):
    """Declare a map, optionally handling its partial failure through a successor."""
    nested = {"step": body}
    if body_input is not None:
        nested["input"] = body_input
    node = {"step": "map", "body": nested}
    nodes = {"items": node}
    if routed:
        node["next"] = {"done": "collect", "failed": "collect"}
        nodes["collect"] = {"step": MapEcho.key}
    return {"nodes": nodes, "results": [{"from": "collect" if routed else "items"}]}


def start_map(actor, items, **kwargs):
    """Reach the real map wait, before any of its body rows execute."""
    workflow = load_workflow(map_document(**kwargs), actor=actor)
    run = start_run(workflow, actor=actor, input={"items": items})
    run_until(run, node="items.body")
    return run, system_queryset(StepRun).get(run=run, node_key="items")


def body_rows(run):
    """Read body identities in the declared item order."""
    return system_queryset(StepRun).filter(run=run, node_key="items.body").order_by("map_index")


@pytest.mark.django_db(transaction=True)
def test_step_change_concerns_deduplicate_run_evidence_without_loading_the_run(execution, register_step):
    """One item's publication retains sibling timelines and existing inherited views."""
    from tests.mtidemo.models import MtiChild, MtiParent

    actor, _sent = execution
    with system_context(reason="tests.map.concerns"):
        children = [MtiChild.objects.create(title=str(index)) for index in range(2)]

    class RecordItem(MapEcho):
        key = "map_record_concerns"

        def run(self, ctx):
            ctx.record(children[0])
            ctx.record(children[ctx.input["index"]], operation="changed")
            return ctx.done(ctx.input)

    register_step(RecordItem)
    run, _mapped = start_map(actor, [{"index": index} for index in range(2)], body=RecordItem.key)
    run_until(run)
    item = body_rows(run).first()
    assert "run" not in item._state.fields_cache
    with CaptureQueriesContext(connection) as captured:
        concerns = item.change_related_records()
    expected = {ChangeRelatedRecord(run._meta.label, run.sqid)} | {
        ChangeRelatedRecord(model._meta.label, model.public_id_from_pk(child.pk))
        for model in (MtiParent, MtiChild) for child in children
    }
    assert set(concerns) == expected and len(concerns) == len(expected)
    selects = [query["sql"] for query in captured if query["sql"].startswith("SELECT")]
    assert len(selects) == 2, captured.captured_queries
    assert any(sql.startswith("SELECT DISTINCT") and '"content_type_id"' in sql for sql in selects)
    assert not any(f'FROM "{run._meta.db_table}"' in sql for sql in selects)
    assert "run" not in item._state.fields_cache
    from angee.base.refs import record_ref_for

    with CaptureQueriesContext(connection) as captured:
        leaf_concerns = ChangeRelatedRecord.for_records(*(record_ref_for(child) for child in children))
    assert len(captured) == 0
    assert set(leaf_concerns) == {concern for concern in expected if concern.model == MtiChild._meta.label}


def test_map_item_is_the_typed_exclusive_result_contract():
    success = MapItem[int].model_validate({"index": 0, "outcome": "done", "output": 7})
    failure = MapItem[int].model_validate({"index": 1, "outcome": "error", "error": "Unavailable."})
    empty = MapItem[int | None].model_validate({"index": 2, "outcome": "done", "output": None})
    observed = MapItem[int].model_validate({"index": 3, "outcome": "error", "output": 7})
    assert success.output == 7
    assert success.model_dump(mode="json") == {"index": 0, "outcome": "done", "output": 7}
    assert failure.model_dump(mode="json") == {"index": 1, "outcome": "error", "error": "Unavailable."}
    assert empty.model_dump(mode="json") == {"index": 2, "outcome": "done", "output": None}
    assert observed.model_dump(mode="json") == {"index": 3, "outcome": "error", "output": 7}
    for invalid in (
        {"index": 0, "outcome": "done"},
        {"index": 0, "outcome": "done", "output": 7, "error": "Unavailable."},
        {"index": 0, "outcome": "done", "error": "Unavailable."},
        {"index": -1, "outcome": "done", "output": 7},
        {"index": 0, "outcome": "done", "output": "not a number"},
    ):
        with pytest.raises(PydanticValidationError):
            MapItem[int].model_validate(invalid)


def test_empty_map_finishes_without_body_rows(execution, map_steps):
    actor, _sent = execution
    run, mapped = start_map(actor, [])
    assert run.status == "succeeded" and run.output == [] and run.outcome == "done"
    assert not body_rows(run).exists()
    assert mapped.is_map and not mapped.is_mapped
    assert mapped.map_total == mapped.map_settled == 0
    assert mapped.attempt == 1
    schema = addon_schema(workflow_schema.schemas, "console")
    assert result_data(execute_schema(schema, "{ steprun { is_map is_mapped map_total } }", user=actor)) == {
        "steprun": [{"is_map": True, "is_mapped": False, "map_total": 0}],
    }


def test_context_map_index_distinguishes_zero_from_an_ordinary_row(execution, map_steps, register_step):
    actor, _sent = execution
    positions = []

    class ObserveIndex(MapEcho):
        def run(self, ctx):
            positions.append((ctx.step_run.node_key, ctx.map_index))
            return ctx.done(ctx.input)

    register_step(ObserveIndex)
    run, _mapped = start_map(actor, [{"value": 0}, {"value": 1}], routed=True)
    run_until(run)
    assert run.status == "succeeded"
    assert positions == [("items.body", 0), ("items.body", 1), ("collect", None)]


def test_map_orders_results_and_bounds_planning(execution, map_steps, settings):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 2
    values = [{"value": index} for index in range(5)]
    run, mapped = start_map(actor, values)
    assert (mapped.status, mapped.waiting_kind) == ("waiting", "map")
    assert (mapped.map_total, mapped.map_settled) == (5, 0)
    assert list(body_rows(run).values_list("map_index", flat=True)) == [0, 1]
    second = body_rows(run).get(map_index=1)
    assert second.is_mapped and second.rank == mapped.rank
    assert runner.execute(second.pk)
    assert list(body_rows(run).values_list("map_index", flat=True)) == [0, 1, 2]
    mapped.refresh_from_db()
    assert (mapped.map_total, mapped.map_settled) == (5, 1)
    run_until(run)
    results = TypeAdapter(list[MapItem[dict[str, int]]]).validate_python(run.output)
    assert [(item.index, item.outcome, item.output) for item in results] == [
        (index, "done", value) for index, value in enumerate(values)
    ]
    assert all("error" not in item for item in run.output)
    mapped.refresh_from_db()
    assert run.status == "succeeded" and mapped.attempt == 2
    assert (mapped.map_total, mapped.map_settled) == (5, 5)
    assert all(row.rank == mapped.rank and row.is_mapped for row in body_rows(run))


def test_routed_body_failure_keeps_typed_partial_results(execution, map_steps, register_step):
    actor, _sent = execution

    class Partial(MapEcho):
        key = "map_partial"

        def run(self, ctx):
            return ctx.fail("Item unavailable.") if ctx.map_index == 1 else ctx.done(ctx.input)

    register_step(Partial)
    run, mapped = start_map(actor, [{"value": index} for index in range(3)], body=Partial.key, routed=True)
    run_until(run)
    mapped.refresh_from_db()
    assert run.status == "succeeded" and mapped.outcome == "failed"
    items = TypeAdapter(list[MapItem[dict[str, int]]]).validate_python(run.output)
    assert [item.index for item in items] == [0, 1, 2]
    assert items[0].output == {"value": 0} and items[2].output == {"value": 2}
    assert items[1].outcome == "error" and items[1].error == "Item unavailable."
    assert "output" not in run.output[1]


def test_map_collection_keeps_attempt_diagnostics_out_of_reader_output(execution, map_steps, register_step):
    actor, _sent = execution

    class Partial(MapEcho):
        key = "map_private_failure"

        def run(self, ctx):
            return ctx.fail("Item unavailable.")

    register_step(Partial)
    run, _mapped = start_map(actor, [{"value": 1}], body=Partial.key, routed=True)
    run_until(run)
    body = body_rows(run).get()
    with system_context(reason="test.map_diagnostic"):
        StepAttempt.objects.filter(step_run=body).update(
            error="ValueError: Item unavailable.; caused by ProviderError: private body",
        )
    assert body_rows(run).collect_map(1) == [{
        "index": 0, "outcome": "error", "error": "Item unavailable.",
    }]


def test_unrouted_body_failure_preserves_siblings_and_retries_only_that_item(
    execution, map_steps, register_step, settings,
):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 3
    visits = []

    class FailOnce(MapEcho):
        key = "map_fail_once"

        def run(self, ctx):
            visits.append((ctx.map_index, ctx.attempt.number))
            if ctx.map_index == 1 and ctx.attempt.number == 1:
                return ctx.fail("Try this item again.")
            return ctx.done(ctx.input)

    register_step(FailOnce)
    run, mapped = start_map(actor, [{"value": index} for index in range(3)], body=FailOnce.key)
    first, failed, sibling = list(body_rows(run))
    assert runner.execute(first.pk) and runner.execute(failed.pk)
    run.refresh_from_db()
    assert run.status == "failed"
    assert not runner.execute(sibling.pk)
    assert list(body_rows(run).values_list("status", flat=True)) == ["succeeded", "failed", "ready"]
    StepRun.objects.retry_step(failed, actor=actor)
    run_until(run)
    assert run.status == "succeeded"
    assert visits == [(0, 1), (1, 1), (1, 2), (2, 1)]
    assert list(body_rows(run).values_list("pk", flat=True)) == [first.pk, failed.pk, sibling.pk]
    assert body_rows(run).get(pk=failed.pk).rank == mapped.rank
    assert [item["output"] for item in run.output] == [{"value": index} for index in range(3)]


def test_waiting_body_consumes_a_slot_and_retry_keeps_its_index(execution, map_steps, register_step, settings):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 1

    class Delayed(MapEcho):
        key = "map_delayed"
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            if ctx.attempt.number == 1:
                raise Retryable("The item is temporarily unavailable.")
            if not ctx.state:
                return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"resumed": True})
            return ctx.done(ctx.input)

    register_step(Delayed)
    run, mapped = start_map(actor, [{"value": 0}, {"value": 1}], body=Delayed.key)
    for index in range(2):
        run_until(run)
        assert body_rows(run).count() == index + 1
        current = body_rows(run).get(map_index=index)
        assert current.status == "waiting" and current.waiting_kind == "time"
        assert runner.wake() == 1
        run_until(run)
        current.refresh_from_db()
        assert current.attempt == 2 and current.status == "waiting"
        assert body_rows(run).count() == index + 1
        assert runner.wake() == 1
        assert runner.execute(current.pk)
    run_until(run)
    assert run.status == "succeeded"
    assert list(body_rows(run).values_list("attempt", flat=True)) == [3, 3]
    assert all(row.rank == mapped.rank for row in body_rows(run))


def test_operator_wait_in_a_body_requires_duplicate_acknowledgement(execution, map_steps, register_step, settings):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 1
    identities = []

    class MarkedItem(MapEcho):
        key = "map_marked_item"
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            identities.append(ctx.idempotency_key)
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                raise Retryable("The effect's result was unavailable.")
            assert ctx.retry_acknowledged
            return ctx.done(ctx.input)

    register_step(MarkedItem)
    run, mapped = start_map(actor, [{"value": 0}], body=MarkedItem.key)
    run_until(run)
    item = body_rows(run).get()
    assert item.status == "waiting" and item.waiting_kind == "error"
    with pytest.raises(ValidationError, match="duplicate"):
        StepRun.objects.retry_step(item, actor=actor)
    StepRun.objects.retry_step(item, actor=actor, accept_duplicate=True)
    run_until(run)
    assert run.status == "succeeded" and identities[0] == identities[1]
    assert body_rows(run).get().rank == mapped.rank
    assert system_queryset(StepAttempt).get(step_run=item, number=2).acknowledged_by_id == actor.pk




class ReviewedValue(BaseModel):
    """A successful review output whose field is absent on expiration."""

    value: int




def test_map_progress_and_body_identity_are_resource_owned(execution, map_steps, settings):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 2
    run, mapped = start_map(actor, [{"value": index} for index in range(3)])
    assert runner.execute(body_rows(run).get(map_index=0).pk)
    schema = addon_schema(workflow_schema.schemas, "console")
    result = result_data(execute_schema(schema, """query($run: String!) {
      steprun(where: {run: {_eq: $run}}, order_by: [{rank: asc}, {map_index: asc}]) {
        node_key display_name rank map_index is_map is_mapped map_total map_settled
      }
    }""", {"run": run.sqid}, user=actor))["steprun"]
    parent = next(row for row in result if not row["is_mapped"])
    assert parent == {
        "node_key": "items", "display_name": "items", "rank": mapped.rank, "map_index": 0,
        "is_map": True, "is_mapped": False, "map_total": 3, "map_settled": 1,
    }
    bodies = [row for row in result if row["is_mapped"]]
    assert [row["map_index"] for row in bodies] == [0, 1, 2]
    assert [row["display_name"] for row in bodies] == [f"items.body [{index}]" for index in range(3)]
    assert all(not row["is_map"] for row in bodies)
    assert all(row["rank"] == mapped.rank and row["map_total"] == row["map_settled"] == 0 for row in bodies)


def test_decision_map_body_has_independent_questions_and_typed_outputs(execution, map_steps, register_step):
    actor, _sent = execution
    reviewer = create_user("map-reviewer")
    reference = vault_for(actor)
    write_relationships([RelationshipTuple(to_object_ref(reference), "viewer", to_subject_ref(reviewer))])
    applied = []

    class ItemReview(DecisionStep[None, None, None]):
        key = "map_review"
        outcomes = {"accepted": "Accepted"}
        def ask(self, ctx):
            return ctx.ask(DecisionRequest(
                kind=self.key, records=(reference,), assignees=(reviewer,),
                proposal=DecisionProposal(alternatives=[{"key": "accept", "label": "Accept", "outcome": "accepted"}]),
            ))
        def continue_with(self, ctx, decision, outcome):
            retained = ctx.decision(public_id_of(decision))
            assert retained.verdict == ["accept"] and retained.answered_by_id == reviewer.pk
            applied.append(ctx.map_index)
            return ctx.done(ctx.input, outcome=outcome)

    register_step(ItemReview)
    run, _ = start_map(actor, [{"value": 0}, {"value": 1}], body=ItemReview.key)
    run_until(run)
    original = list(body_rows(run))
    decisions = [system_queryset(Decision).get(requesting_steps=row) for row in original]
    assert decisions[0].pk != decisions[1].pk
    decide(decisions[1], actor=reviewer, chosen=["accept"])
    run_until(run)
    assert applied == [1] and body_rows(run)[0].status == "waiting"
    decide(decisions[0], actor=reviewer, chosen=["accept"])
    run_until(run)
    assert run.status == "succeeded" and applied == [1, 0]
    assert [item["output"] for item in system_queryset(StepRun).get(run=run, node_key="items").output] == [
        {"value": 0}, {"value": 1},
    ]
