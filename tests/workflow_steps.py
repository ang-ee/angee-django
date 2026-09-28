"""Neutral step implementations used by the L0/L1 workflow proofs."""

from datetime import timedelta

import pytest
from django.apps import apps
from pydantic import BaseModel

from angee.workflows.steps import Retryable, RetryPolicy, Step


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
    """Fail transiently once, then complete in the same step row."""

    key = "retry"
    retry = RetryPolicy(max_attempts=2, backoff=timedelta())

    def run(self, ctx):
        """Exercise attempt counters independently of dispatches."""
        if ctx.attempt.number == 1:
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
