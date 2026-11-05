"""Reviewer probes: StepContext.record admission reuse must fail closed."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from tests.conftest import vault_for
from tests.workflow_steps import Echo

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


def test_probe_record_reproves_after_revision_or_other_action(execution, register_step):
    actor, _ = execution
    subject = vault_for(actor)
    statements: dict[str, int] = {}

    class Probe(Echo):
        key = "write_path_record_probe"

        def run(self, ctx):
            row = ctx.load(type(subject), subject.sqid)
            with CaptureQueriesContext(connection) as queries:
                ctx.record(row)
            statements["admitted"] = len(queries)
            if "revision" in row.__dict__:
                row.__dict__["revision"] = row.__dict__["revision"] + 1
                with CaptureQueriesContext(connection) as queries:
                    ctx.record(row)
                statements["revision_moved"] = len(queries)
            other = ctx.load(type(subject), subject.sqid, permission="write")
            with CaptureQueriesContext(connection) as queries:
                ctx.record(other)
            statements["write_admission"] = len(queries)
            fresh = type(subject)._base_manager.get(pk=subject.pk)
            with CaptureQueriesContext(connection) as queries:
                ctx.record(fresh)
            statements["outside_context"] = len(queries)
            return ctx.done(ctx.input)

    register_step(Probe)
    workflow = load_workflow({"nodes": {"entry": {"step": Probe.key}}}, actor=actor)
    run_until(start_run(workflow, actor=actor, subject=subject))
    print(f"RV record statements: {statements}")
    assert all(count > 0 for key, count in statements.items() if key != "admitted")
