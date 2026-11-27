"""Neutral step implementations used by the L0/L1 workflow proofs."""

from datetime import timedelta

import pytest
from django.apps import apps
from pydantic import BaseModel

from angee.workflows.awaits import AwaitRunInput
from angee.workflows.steps import Retryable, RetryPolicy, Step
from angee.workflows.testing.drivers import load_workflow


class Value(BaseModel):
    """A scalar envelope passed between nodes."""

    value: int = 0


class RouteConfig(BaseModel):
    """The declared branch chosen by the fixture step."""

    outcome: str = "done"


class Echo(Step[Value, Value, None]):
    """Return the admitted scalar unchanged."""

    key = "echo"

    def run(self, ctx):
        """Complete using the parsed input."""
        return ctx.done(ctx.input)


class Route(Step[Value, Value, RouteConfig]):
    """Choose a declared named outcome from config."""

    key = "route"
    outcomes = {"done": "Done", "left": "Left", "right": "Right"}

    def run(self, ctx):
        """Emit the configured outcome."""
        return ctx.done(ctx.input, outcome=ctx.config.outcome)


class Pause(Step[Value, Value, None]):
    """Wait once, preserving a checkpoint and stable idempotency identity."""

    key = "pause"

    def run(self, ctx):
        """Return after a due time wake."""
        if ctx.state:
            return ctx.done(ctx.input)
        return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"resumed": True})


class Retry(Step[Value, Value, None]):
    """Fail transiently for as many attempts as the input value, then complete in the same step row."""

    key = "retry"
    retry = RetryPolicy(max_attempts=2, backoff=timedelta())

    def run(self, ctx):
        """Exercise attempt counters independently of dispatches."""
        if ctx.attempt.number <= ctx.input.value:
            raise Retryable("Try again.")
        return ctx.done(ctx.input)


class Reject(Step[Value, Value, None]):
    """Fail permanently so routing can recover through error."""

    key = "reject"

    def run(self, ctx):
        """Produce the built-in failure outcome."""
        return ctx.fail("Rejected by the fixture.")


class Visible(Step[Value, Value, None]):
    """Count plain ORM rows under the execution actor."""

    key = "visible"

    def run(self, ctx):
        """Read without an explicit actor or system queryset."""
        return ctx.done({"value": apps.get_model("knowledge", "Vault").objects.count()})


@pytest.fixture
def workflow_step_classes(register_step):
    """Contribute neutral steps through the normal registry setting."""
    for step in (Echo, Route, Pause, Retry, Reject, Visible):
        register_step(step)


def document(*keys, step="echo"):
    """Build a linear declaration with a result producer."""
    return {
        "nodes": {
            key: {"step": step, **({"next": {"done": keys[index + 1]}} if index + 1 < len(keys) else {})}
            for index, key in enumerate(keys)
        },
        "results": [{"from": keys[-1]}],
    }


@pytest.fixture
def child_graph(execution, register_step):
    """Build parents whose child admission is performed by a real claimed step."""
    actor, sent = execution
    admitted = []

    def build(*, relation="owned", await_child=True, repeat=False, next_page=False, request_key=None, subject=None,
              subject_model="", run_actor=None, grant_child=True, child_step="echo"):
        child_workflow = load_workflow(
            document("entry", step=child_step), key=f"child-{len(admitted)}", actor=actor,
            subject_model=subject_model,
        )

        class StartChild(Step[Value, AwaitRunInput, None]):
            """Start again after a time wait when exercising derived-key replay."""

            key = "start_child"

            def run(self, ctx):
                child = ctx.start_run(
                    child_workflow, input=ctx.input.model_dump(), relation=relation,
                    request_key=request_key, subject=subject,
                )
                admitted.append(child)
                if repeat and not ctx.state:
                    if next_page:
                        return ctx.next_page({"again": True})
                    return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"again": True})
                return ctx.done({"run_id": str(child.sqid)})

        class Finish(Step[None, None, None]):
            """Close every observed child outcome without requiring a value on cancellation."""

            key = "finish_child"

            def run(self, ctx):
                return ctx.done(ctx.input)

        register_step(StartChild)
        register_step(Finish)
        nodes = {"start": {"step": StartChild.key}}
        if await_child:
            nodes["start"]["next"] = {"done": "await"}
            nodes["await"] = {
                "step": "await_run", "config": {"expects": child_workflow.key},
                "next": {outcome: "finish" for outcome in ("done", "error", "canceled", "interrupted")},
            }
            nodes["finish"] = {"step": Finish.key}
        parent_workflow = load_workflow({"nodes": nodes}, key=f"parent-{len(admitted)}", actor=actor)
        if run_actor is not None:
            parent_workflow.with_actor(actor).grant_record_access("starter", run_actor)
            if grant_child:
                child_workflow.with_actor(actor).grant_record_access("starter", run_actor)
        runs = apps.get_model("workflows", "WorkflowRun").objects
        parent = runs.start(parent_workflow, actor=run_actor or actor, input={"value": 7})
        return parent, child_workflow

    return actor, sent, admitted, build
