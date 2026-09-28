"""Synchronous test drivers using the production workflow manager verbs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from rebac import system_context

from angee.resources.entries import ROW_KIND, ResourceEntry, resource_manifest_for
from angee.workflows.states import StepRunStatus


def load_workflow(
    document_or_addon_key: dict[str, Any] | str,
    *,
    key: str = "test-workflow",
    actor: Any = None,
    name: str = "",
    publish: bool = True,
    subject_model: str = "",
) -> Any:
    """Install a document or ``addon.name:resource_xref`` with production validation.

    Resource keys read the declared workflow row with the resources addon's
    parser. Installation still belongs to ``WorkflowManager.install_definition``;
    tests of the import ledger should use ``Resource.objects.load_addons``.
    """

    model = apps.get_model("workflows", "Workflow")
    if isinstance(document_or_addon_key, str):
        addon_name, separator, xref = document_or_addon_key.partition(":")
        if not separator or not xref:
            raise ValueError("A workflow resource key must be 'addon.name:resource_xref'.")
        addon = next((config for config in apps.get_app_configs() if config.name == addon_name), None)
        if addon is None:
            raise LookupError(f"Addon {addon_name!r} is not installed.")
        for tier, declarations in sorted(resource_manifest_for(addon).items()):
            for declaration in declarations:
                entry = ResourceEntry.from_declaration(addon, tier, declaration)
                if entry.kind != ROW_KIND:
                    continue
                for group in entry.read_groups():
                    if group.model != model:
                        continue
                    for row in group.dataset.dict:
                        if row.pop("_xref") == xref:
                            return model.objects.install_definition(**row, actor=actor)
        raise LookupError(f"Workflow resource {document_or_addon_key!r} was not found.")
    return model.objects.install_definition(
        key=key, name=name or key, draft=document_or_addon_key,
        publish=publish, subject_model=subject_model, actor=actor,
    )


def start_run(workflow: Any, *, actor: Any, **kwargs: Any) -> Any:
    """Start a run through the composed workflow run manager."""

    model = apps.get_model("workflows", "WorkflowRun")
    return model.objects.start(workflow, actor=actor, **kwargs)


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
