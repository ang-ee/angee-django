"""The public harness composes registration, admission and node execution."""

from typing import Any

import pytest

from angee.base.scoping import system_queryset
from angee.workflows.states import StepRunStatus
from angee.workflows.steps import Step
from angee.workflows.testing import drivers
from angee.workflows.testing.models import StepRun
from tests.workflow_steps import Value, document


def test_registered_local_step_runs_through_the_production_driver(execution, register_step):
    """A function-local step participates in normal definition validation and execution."""

    class Increment(Step[Value, Value, None]):
        """Increment one value to prove the registered body was executed."""

        key = "increment"

        def run(self, ctx: Any):
            """Return the declared output through the normal settlement contract."""
            return ctx.done({"value": ctx.input.value + 1})

    register_step(Increment)
    actor, sent = execution
    workflow = drivers.load_workflow(document("entry", step="increment"), actor=actor)
    run = drivers.start_run(workflow, actor=actor, input={"value": 4})

    drivers.run_until(run)

    assert run.output == {"value": 5}
    assert len(sent) == 1


@pytest.mark.parametrize("status", [StepRunStatus.READY, StepRunStatus.SUCCEEDED])
def test_run_factory_reaches_node_with_real_predecessor_output(execution, run_factory, status):
    """Factory placement executes predecessors and honors the requested target status."""

    actor, _sent = execution
    workflow = drivers.load_workflow(document("first", "second"), actor=actor)

    run = run_factory(workflow).at("second", status=status, input={"value": 7})

    step_runs = {step_run.node_key: step_run for step_run in system_queryset(StepRun).filter(run=run)}
    assert step_runs["first"].status == StepRunStatus.SUCCEEDED
    assert step_runs["first"].output == {"value": 7}
    assert step_runs["second"].status == status
    if status == StepRunStatus.SUCCEEDED:
        assert step_runs["second"].input == {"value": 7}
        assert run.output == {"value": 7}


def test_run_factory_reaches_a_real_wait(execution, run_factory):
    """A requested waiting status preserves the target's actual checkpoint."""

    actor, _sent = execution
    workflow = drivers.load_workflow(document("entry", step="pause"), actor=actor)

    run = run_factory(workflow).at("entry", status=StepRunStatus.WAITING)

    step_run = system_queryset(StepRun).get(run=run)
    assert step_run.state == {"resumed": True}
    assert step_run.attempts.with_actor(actor).count() == 1


def test_resource_key_names_the_addon_and_resource():
    """Malformed resource selectors fail before touching persistence."""

    with pytest.raises(ValueError, match="addon.name:resource_xref"):
        drivers.load_workflow("unqualified")
