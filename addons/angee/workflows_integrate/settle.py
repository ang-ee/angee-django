"""Idempotent terminal telemetry for a retained Bridge subject."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias, related_on
from angee.base.identity import public_id_for
from angee.base.impl import resolve_all_impl_classes
from angee.integrate.errors import IntegrationError
from angee.integrate.models import Bridge
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.steps import StepImpl
from angee.workflows_integrate.steps import BoundedStreamStage, StreamStageOutput


def settle_bridge_run(run: Any, *, using: str | None = None) -> None:
    """Settle exactly the current run, at most once, under the Bridge row lock.

    The run pointer remains as inspection evidence. The busy-stage predicate is
    the second CAS component: repeated deliveries cannot move cadence twice.
    Never copy untrusted workflow/provider error text into integration telemetry.
    """

    using = get_write_alias(type(run), using=using, instance=run)
    if not run.is_terminal:
        return
    content_type = related_on(run, "subject_content_type", using=using)
    model = None if content_type is None else content_type.model_class()
    if model is None or not issubclass(model, Bridge):
        return
    with system_context(reason="workflows_integrate.settle"), transaction.atomic(using=using):
        bridge = model.objects.db_manager(using).lock_if_supported().filter(pk=run.subject_object_id).first()
        if bridge is None:
            return
        expected = public_id_for(type(run), run.pk)
        if bridge.sync_progress.get("details", {}).get("run") != expected or bridge.sync_stage not in (
            bridge.SyncStage.QUEUED,
            *bridge.LIVE_SYNC_STAGES,
        ):
            return
        now = timezone.now()
        if run.status == RunStatus.SUCCEEDED:
            # Each stream step labels its count as final-page work. The data
            # protocol does not promise a whole-cycle count across commit/replay.
            steps = run._meta.apps.get_model("workflows", "StepRun")
            stream_keys = [
                implementation.key
                for implementation in resolve_all_impl_classes("ANGEE_WORKFLOW_STEP_CLASSES", StepImpl)
                if issubclass(implementation, BoundedStreamStage)
            ]
            outputs = (
                steps.objects.db_manager(using)
                .filter(
                    run_id=run.pk,
                    status=StepRunStatus.SUCCEEDED,
                    step__step_class__in=stream_keys,
                )
                .values_list("output", flat=True)
            )
            items = sum(StreamStageOutput.model_validate(output).counts["page_items"] for output in outputs)
            bridge.record_sync(items, now=now, using=using)
        else:
            message = "Sync workflow was canceled." if run.status == RunStatus.CANCELED else "Sync workflow failed."
            bridge.record_sync_error(IntegrationError(message), now=now, using=using)
