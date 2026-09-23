"""Idempotent terminal telemetry for a retained Bridge subject."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

from django.core.signals import setting_changed
from django.db import transaction
from django.dispatch import receiver
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


@cache
def _stream_step_keys() -> tuple[str, ...]:
    return tuple(
        implementation.key
        for implementation in resolve_all_impl_classes("ANGEE_WORKFLOW_STEP_CLASSES", StepImpl)
        if issubclass(implementation, BoundedStreamStage)
    )


@receiver(setting_changed)
def _refresh_stream_step_keys(*, setting: str, **kwargs: Any) -> None:
    if setting == "ANGEE_WORKFLOW_STEP_CLASSES":
        _stream_step_keys.cache_clear()
        _stream_step_keys()


_stream_step_keys()


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
        progress = bridge.sync_progress if isinstance(bridge.sync_progress, Mapping) else {}
        details = progress.get("details")
        pointer = details.get("run") if isinstance(details, Mapping) else None
        if pointer != expected or bridge.sync_stage not in (
            bridge.SyncStage.QUEUED,
            *bridge.LIVE_SYNC_STAGES,
        ):
            return
        now = timezone.now()
        if run.status == RunStatus.SUCCEEDED:
            steps = run._meta.apps.get_model("workflows", "StepRun")
            outputs = (
                steps.objects.db_manager(using)
                .filter(
                    run_id=run.pk,
                    status=StepRunStatus.SUCCEEDED,
                    step__step_class__in=_stream_step_keys(),
                )
                .values_list("output", flat=True)
            )
            items = sum(StreamStageOutput.model_validate(output).counts["cycle_items"] for output in outputs)
            bridge.record_sync(items, now=now, using=using)
        else:
            message = "Sync workflow was canceled." if run.status == RunStatus.CANCELED else "Sync workflow failed."
            bridge.record_sync_error(IntegrationError(message), now=now, using=using)
