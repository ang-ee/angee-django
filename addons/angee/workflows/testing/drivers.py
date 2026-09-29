"""Synchronous test drivers using the production workflow manager verbs."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from rebac import system_context

from angee.workflows.states import StepRunStatus
from angee.workflows.steps import Step


@contextmanager
def register_steps(*steps: type[Step[Any, Any, Any]]) -> Iterator[None]:
    """Register temporary classes, restoring registry entries and module names on exit.

    Function-local classes use the same import-path registry as production
    classes. Native mock patches isolate nested registrations without restoring
    unrelated settings changed by an independently managed fixture.
    """

    with ExitStack() as stack:
        registry = {}
        for step in steps:
            stack.enter_context(patch.object(sys.modules[step.__module__], step.__name__, step, create=True))
            registry[step.key] = f"{step.__module__}.{step.__name__}"
        stack.enter_context(patch.dict(settings.ANGEE_WORKFLOW_STEP_CLASSES, registry))
        yield


def load_workflow(
    document_or_xref: dict[str, Any] | str,
    *,
    key: str = "test-workflow",
    actor: Any = None,
    name: str = "",
    publish: bool = True,
    subject_model: str = "",
    allow_non_dev: bool = False,
) -> Any:
    """Install a document or canonical ``addon.name.xref`` resource declaration.

    Resource rows retain their authored fields, including publication intent,
    through the native resource adapter and ledger. ``allow_non_dev`` passes
    through the resource owner's explicit override of the demo-tier guard.
    """

    model = apps.get_model("workflows", "Workflow")
    if isinstance(document_or_xref, str):
        ledger = apps.get_model("resources", "Resource")
        return ledger.objects.load_xref(document_or_xref, model=model, actor=actor, allow_non_dev=allow_non_dev)
    return model.objects.install_definition(
        key=key, name=name or key, draft=document_or_xref,
        publish=publish, subject_model=subject_model, actor=actor,
    )


def start_run(workflow: Any, *, actor: Any, **kwargs: Any) -> Any:
    """Start a run through the composed workflow run manager."""

    model = apps.get_model("workflows", "WorkflowRun")
    return model.objects.start(workflow, actor=actor, **kwargs)


def decide(decision: Any, *, actor: Any, action: str, values: dict[str, Any] | None = None) -> Any:
    """Submit the caller's observed revision through the real decisions owner."""
    return type(decision).objects.decide(
        decision.pk, actor=actor, revision=decision.revision, action=action, values={} if values is None else values,
    )


def run_until(run: Any, *, node: str | None = None, max_steps: int = 100) -> Any:
    """Execute ready rows until terminal or before the requested node executes.

    A waiting run with no ready row returns to the caller, which controls time
    and calls the production tick when appropriate. Task sends should be captured
    at ``current_app.send_task`` by the adopting test suite.
    """

    for _ in range(max_steps):
        with system_context(reason="workflows.testing inspect execution"):
            run.refresh_from_db()
        if run.is_terminal:
            return run
        with system_context(reason="workflows.testing inspect ready rows"):
            ready = list(run.step_runs.filter(status=StepRunStatus.READY).order_by("pk"))
        if not ready or any(step_run.node_key == node for step_run in ready):
            return run
        for step_run in ready:
            run.step_runs.execute(step_run.pk)
    raise AssertionError(f"Workflow did not settle after {max_steps} iterations.")


@dataclass(frozen=True)
class RunFactory:
    """Reach a node through real predecessors instead of fabricating step run rows.

    Bind the workflow and acting user once, then call ``at`` for independent
    runs. A non-ready status executes the target once and checks its real
    settlement; it never manufactures an impossible graph state.
    """

    workflow: Any
    actor: Any

    def at(self, node: str, *, status: str = str(StepRunStatus.READY), **kwargs: Any) -> Any:
        """Return a new run stopped at the requested node and status."""

        self.workflow.published.definition.node(node)
        run = start_run(self.workflow, actor=self.actor, **kwargs)
        run_until(run, node=node)
        with system_context(reason="workflows.testing inspect target"):
            step_run = run.step_runs.get(node_key=node)
        if step_run.status != status and step_run.status == StepRunStatus.READY:
            run.step_runs.execute(step_run.pk)
            with system_context(reason="workflows.testing inspect settlement"):
                step_run.refresh_from_db()
                run.refresh_from_db()
        if step_run.status != status:
            raise AssertionError(f"Node {node!r} reached {step_run.status!r}, expected {status!r}.")
        return run
