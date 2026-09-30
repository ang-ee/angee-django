"""Map bodies use the ordinary planner, settlements, recovery and read resources."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models.functions import Now
from pydantic import BaseModel, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac import system_context

from angee.base.identity import public_id_of
from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.workflows import schema as workflow_schema
from angee.workflows.maps import MapItem
from angee.workflows.reviews import ReviewStep
from angee.workflows.steps import EmptyOutput, Retryable, RetryPolicy, Step
from angee.workflows.testing.drivers import decide, load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRun
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.decisions_models import Decision, DecisionGroup


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
    assert StepRun.objects.execute(second.pk)
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
    assert StepRun.objects.execute(first.pk) and StepRun.objects.execute(failed.pk)
    run.refresh_from_db()
    assert run.status == "failed"
    assert not StepRun.objects.execute(sibling.pk)
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
        assert StepRun.objects.wake() == 1
        run_until(run)
        current.refresh_from_db()
        assert current.attempt == 2 and current.status == "waiting"
        assert body_rows(run).count() == index + 1
        assert StepRun.objects.wake() == 1
        assert StepRun.objects.execute(current.pk)
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
        mode = "IO"
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
    assert item.status == "waiting" and item.waiting_kind == "operator"
    with pytest.raises(ValidationError, match="duplicate"):
        StepRun.objects.retry_step(item, actor=actor)
    StepRun.objects.retry_step(item, actor=actor, accept_duplicate=True)
    run_until(run)
    assert run.status == "succeeded" and identities[0] == identities[1]
    assert body_rows(run).get().rank == mapped.rank
    assert system_queryset(StepAttempt).get(step_run=item, number=2).acknowledged_by_id == actor.pk


class MapAccept(Action, key="accept", label="Accept", verdict=Verdict.COMPLETED, outcome="accepted"):
    """A typed answer shared by independent item reviews."""


class ReviewedValue(BaseModel):
    """A successful review output whose field is absent on expiration."""

    value: int


def test_expired_typed_review_body_reaches_a_typed_map_collector(execution, register_step):
    """The empty-output outcome belongs in the map's published and runtime contract."""
    actor, _sent = execution
    reviewer = create_user("map-expiry-reviewer")

    class ExpiringReview(ReviewStep[None, ReviewedValue, None, None]):
        key = "map_expiring_review"
        actions = (MapAccept,)

        def ask(self, ctx):
            return ctx.ask(DecisionRequest(
                kind=self.key, subject=None, assignees=(reviewer,), actions=self.actions,
                expires_at=ctx.now + timedelta(days=1),
            ))

        def apply(self, ctx, settled):
            raise AssertionError("An expired unanswered review must not apply an answer.")

    class CollectReview(Step[list[MapItem[ReviewedValue | EmptyOutput]], None, None]):
        key = "map_collect_review"

        def run(self, ctx):
            item = ctx.input[0]
            assert isinstance(item, MapItem) and isinstance(item.output, EmptyOutput)
            return ctx.done({"index": item.index, "outcome": item.outcome, "value": item.output.model_dump()})

    register_step(ExpiringReview)
    register_step(CollectReview)
    workflow = load_workflow({
        "nodes": {
            "items": {"step": "map", "body": {"step": ExpiringReview.key}, "next": {"done": "collect"}},
            "collect": {"step": CollectReview.key},
        },
        "results": [{"from": "collect"}],
    }, actor=actor)
    run = start_run(workflow, actor=actor, input={"items": [{"value": 1}]})
    run_until(run)
    item = body_rows(run).get()
    seat = system_queryset(Decision).get(group_id=item.decision_group_id)
    with system_context(reason="test elapsed map review deadline"):
        Decision.objects.filter(pk=seat.pk).owner_update(expires_at=Now() - timedelta(seconds=1))
    assert Decision.objects.expire_due() == 1
    run_until(run)
    assert run.status == "succeeded"
    assert run.output == {"index": 0, "outcome": "expired", "value": {}}
    assert system_queryset(StepRun).get(run=run, node_key="items").output == [
        {"index": 0, "outcome": "expired", "output": {}},
    ]
    assert system_queryset(Decision).get(pk=seat.pk).closed_reason == "expired"


def test_review_body_has_independent_groups_reasks_and_typed_resolution(execution, map_steps, register_step):
    actor, _sent = execution
    reviewer = create_user("map-reviewer")
    applied = []

    class ItemReview(ReviewStep[None, None, None, None]):
        key = "map_review"
        actions = (MapAccept,)

        def ask(self, ctx):
            return ctx.ask(DecisionRequest(
                kind=self.key, subject=None, assignees=(reviewer,), actions=self.actions,
            ))

        def apply(self, ctx, settled):
            answer = settled[0]
            retained = ctx.resolution(public_id_of(answer.decision))
            assert isinstance(retained.action, MapAccept) and retained.resolver.pk == reviewer.pk
            assert ctx.actor.pk == ctx.run.run_as_id
            if ctx.map_index == 0 and ctx.state["review_round"] == 1:
                raise ValidationError({"answer": "Please revise this item's answer."})
            applied.append((ctx.map_index, answer.decision.group_id))
            return ctx.done(ctx.input, outcome="accepted")

    register_step(ItemReview)
    run, _mapped = start_map(actor, [{"value": 0}, {"value": 1}], body=ItemReview.key)
    run_until(run)
    original = list(body_rows(run))
    groups = [row.decision_group_id for row in original]
    assert len(set(groups)) == 2 and None not in groups
    for row in original:
        seat = system_queryset(Decision).get(group_id=row.decision_group_id)
        decide(seat, actor=reviewer, action="accept")
    run_until(run)
    first, second = list(body_rows(run))
    assert first.status == "waiting" and second.status == "succeeded"
    assert first.decision_group_id != groups[0] and second.decision_group_id == groups[1]
    assert [group.pk for group in system_queryset(DecisionGroup).get(pk=first.decision_group_id).rounds()] == [
        first.decision_group_id, groups[0],
    ]
    replacement = system_queryset(Decision).get(group_id=first.decision_group_id)
    decide(replacement, actor=reviewer, action="accept")
    run_until(run)
    assert run.status == "succeeded"
    assert applied == [(1, groups[1]), (0, first.decision_group_id)]
    assert [item["outcome"] for item in run.output] == ["accepted", "accepted"]
    assert system_queryset(StepAttempt).filter(step_run__run=run).count() == 7


def test_map_progress_and_body_identity_are_resource_owned(execution, map_steps, settings):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 2
    run, mapped = start_map(actor, [{"value": index} for index in range(3)])
    assert StepRun.objects.execute(body_rows(run).get(map_index=0).pk)
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
