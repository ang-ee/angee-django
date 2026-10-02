"""Bridge-owned admission and settlement of a durable workflow sync cycle."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from rebac import system_context

from angee.integrate.errors import IntegrationError
from angee.integrate.models import Bridge
from angee.integrate.sync import SyncDispatch
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.subjects import RunSubject
from angee.workflows_integrate.steps import StreamStage, StreamStageOutput


class SyncCycleBridge(RunSubject):
    """Opt a concrete Integration/Bridge into workflow-owned sync cycles.

    Place this mixin before ``Bridge`` and the Integration parent. Declare an
    installed ``sync_workflow_key`` and implement the two database-only hooks.
    The scheduler owns queue tokens and its advisory lock; the engine owns the
    run lock and then locks this subject before admission and settlement.
    """

    sync_workflow_key: ClassVar[str]

    def sync_workflow_input(self) -> Any:
        """Snapshot the workflow input after acquiring the declared scope locks."""
        raise NotImplementedError

    def lock_sync_scope(self) -> None:
        """Lock upstream scope records in deterministic order; perform no IO."""
        raise NotImplementedError

    def _sync_owner(self) -> Any:
        owner = cast(Any, self).owner
        if owner is None or not owner.is_active:
            raise PermissionDenied("Bridge cycles require an active Integration owner.")
        return owner

    def connect(self, **kwargs: Any) -> None:
        """Connect and grant the Integration owner start access to its workflow."""
        bridge = cast(Any, self)
        bridge.require_access("write")
        with transaction.atomic():
            owner = self._sync_owner()
            cast(Any, super()).connect(**kwargs)
            with system_context(reason="workflows_integrate.connect"):
                workflow = apps.get_model("workflows", "Workflow").objects.get(key=self.sync_workflow_key)
                workflow.grant_record_access("starter", owner)

    def sync(self) -> SyncDispatch:
        """Admit the queued occurrence once, retaining its original input on replay."""
        bridge = cast(Any, self)
        with system_context(reason="workflows_integrate.sync"), transaction.atomic():
            self.lock_sync_scope()
            bridge.refresh_from_db()
            queued_at = bridge.sync_progress.get("queued_at")
            if not queued_at:
                raise ValidationError("Queue the Bridge before dispatching a cycle.")
            owner = self._sync_owner()
            content_type = ContentType.objects.get_for_model(bridge, for_concrete_model=False)
            request_key = f"bridge-sync:{content_type.pk}:{bridge.pk}:{queued_at}"
            runs = apps.get_model("workflows", "WorkflowRun").objects
            existing = runs.filter(request_key=request_key).first()
            workflow = apps.get_model("workflows", "Workflow").objects.get(key=self.sync_workflow_key)
            snapshot = existing.input if existing is not None else self.sync_workflow_input()
            runs.start(workflow, actor=owner, subject=bridge, input=snapshot, request_key=request_key)
        bridge.refresh_from_db()
        return SyncDispatch.DISPATCHED

    def admit_run(self, run: Any) -> None:
        """Refuse another active root cycle and claim this run under the subject lock."""
        bridge = cast(Any, self)
        if run.run_as_id != self._sync_owner().pk:
            raise PermissionDenied("Bridge cycles must run as the Integration owner.")
        active = type(run).objects.for_subject(bridge).filter(parent_step__isnull=True).exclude(
            status__in=RunStatus.terminal_values(),
        ).exclude(pk=run.pk)
        if active.exists():
            raise ValidationError("This Bridge already has an active cycle.")
        if bridge.sync_run_id == run.pk and bridge.sync_is_dispatched:
            return
        if not bridge.claim_dispatch(run.pk):
            raise ValidationError("The Bridge dispatch changed during admission.")

    def settle_run(self, run: Any, status: str) -> None:
        """Project the first terminal transition through the Bridge compare-and-set."""
        settle_cycle(cast(Bridge, self), run, status)


def settle_cycle(bridge: Bridge, run: Any, status: str) -> None:
    """Sum successful stream outputs, or report a safe terminal failure reason."""
    if status == RunStatus.SUCCEEDED:
        definition = run.policy_version.definition
        keys = [key for key, _, _ in definition.declarations() if issubclass(definition.step(key), StreamStage)]
        outputs = run.step_runs.filter(node_key__in=keys, status=StepRunStatus.SUCCEEDED).values_list(
            "output", flat=True,
        )
        items = sum(StreamStageOutput.model_validate(output).counts["cycle_items"] for output in outputs)
        bridge.settle_dispatch(run.pk, result=items)
    else:
        message = "Sync workflow was canceled." if status == RunStatus.CANCELED else "Sync workflow failed."
        bridge.settle_dispatch(run.pk, error=IntegrationError(message))
