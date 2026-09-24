"""Idempotent terminal telemetry for a retained Bridge subject."""

from __future__ import annotations

from typing import Any

from rebac import system_context

from angee.base.db import get_write_alias, related_on
from angee.base.impl import resolve_all_impl_classes
from angee.integrate.errors import IntegrationError
from angee.integrate.models import Bridge
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.steps import StepImpl
from angee.workflows_integrate.steps import BoundedStreamStage, StreamStageOutput


def _stream_step_keys() -> tuple[str, ...]:
    return tuple(
        implementation.key
        for implementation in resolve_all_impl_classes("ANGEE_WORKFLOW_STEP_CLASSES", StepImpl)
        if issubclass(implementation, BoundedStreamStage)
    )


def settle_bridge_run(run: Any, *, using: str | None = None) -> None:
    """Project the terminal outcome through Bridge's expected-run settlement.

    Never copy untrusted workflow/provider error text into integration telemetry.
    """

    using = get_write_alias(type(run), using=using, instance=run)
    if not run.is_terminal:
        return
    content_type = related_on(run, "subject_content_type", using=using)
    model = None if content_type is None else content_type.model_class()
    if model is None or not issubclass(model, Bridge):
        return
    with system_context(reason="workflows_integrate.settle"):
        bridge = model.objects.db_manager(using).filter(pk=run.subject_object_id).first()
        if bridge is None:
            return
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
            bridge.settle_dispatch(run.pk, result=items, using=using)
        else:
            message = "Sync workflow was canceled." if run.status == RunStatus.CANCELED else "Sync workflow failed."
            bridge.settle_dispatch(run.pk, error=IntegrationError(message), using=using)
