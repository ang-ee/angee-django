"""Typed admission contracts shared by workflow test persistence and APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows.models import RunStatus, StepRunStatus, WorkflowDispatchKind


def _workflow_model(run: Any, name: str) -> type[models.Model]:
    """Resolve one composed workflow model from the run's own app registry."""

    return run._meta.apps.get_model("workflows", name)


def _run_tree(root: Any) -> list[Any]:
    """Return a root and its owned-call/continuation descendants in stable order."""

    run_model = _workflow_model(root, "WorkflowRun")
    runs = [root]
    seen = {root.pk}
    while True:
        with system_context(reason="workflows.testing run tree"):
            children = list(
                run_model.objects.filter(
                    parent_step_run__run_id__in=tuple(seen),
                    parent_relation__in=("owned_call", "continuation"),
                ).order_by("pk")
            )
        added = [child for child in children if child.pk not in seen]
        if not added:
            return runs
        runs.extend(added)
        seen.update(child.pk for child in added)


def _retained_run_targets(root: Any) -> tuple[int, ...]:
    """Return WorkflowRun artifacts currently awaited by the run tree."""

    step_artifact = _workflow_model(root, "StepArtifact")
    run_model = _workflow_model(root, "WorkflowRun")
    tree_ids = [current.pk for current in _run_tree(root)]
    with system_context(reason="workflows.testing retained run targets"):
        run_type = ContentType.objects.get_for_model(run_model)
        return tuple(
            step_artifact.objects.filter(
                attempt__step_run__run_id__in=tree_ids,
                attempt__step_run__current_attempt_id=F("attempt_id"),
                attempt__step_run__status=StepRunStatus.WAITING,
                attempt__step_run__waiting_kind="external",
                target_content_type=run_type,
            )
            .order_by("target_object_id")
            .values_list("target_object_id", flat=True)
            .distinct()
        )


def _deliver_results(root: Any, *, now: datetime | None = None) -> None:
    """Consume due cancellation and result intents for the exact run tree."""

    dispatch_model = _workflow_model(root, "WorkflowDispatch")
    run_model = _workflow_model(root, "WorkflowRun")
    timestamp = now or timezone.now()
    tree_ids = {current.pk for current in _run_tree(root)}
    descendant_ids = tree_ids - {root.pk}
    retained_ids = set(_retained_run_targets(root))
    with system_context(reason="workflows.testing due cancellations"):
        cancellations = list(
            dispatch_model.objects.filter(
                Q(kind=WorkflowDispatchKind.CHILD_CANCEL, run_id__in=descendant_ids)
                | Q(kind=WorkflowDispatchKind.RUN_CANCEL, run_id__in=retained_ids),
                available_at__lte=timestamp,
                consumed_at__isnull=True,
            ).order_by("pk")
        )
    for dispatch in cancellations:
        if dispatch.kind == WorkflowDispatchKind.CHILD_CANCEL:
            engine.cancel_child_dispatch(dispatch.pk, expected_child_id=dispatch.run_id)
        else:
            engine.cancel_run_dispatch(dispatch.pk, expected_run_id=dispatch.run_id)

    delivery_targets = descendant_ids | retained_ids
    with system_context(reason="workflows.testing due artifact deliveries"):
        run_type = ContentType.objects.get_for_model(run_model)
        deliveries = list(
            dispatch_model.objects.filter(
                kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
                artifact_content_type=run_type,
                artifact_object_id__in=delivery_targets,
                available_at__lte=timestamp,
                consumed_at__isnull=True,
            ).order_by("pk")
        )
    for dispatch in deliveries:
        engine.deliver_artifact_dispatch(dispatch.pk, now=timestamp)


def start_run(workflow: Any, *, subject: Any = None, actor: Any = None) -> Any:
    """Start a workflow run without relying on a live queue."""

    return engine.start(workflow, subject=subject, actor=actor)


def advance_once(run: Any, *, now: datetime | None = None) -> list[Any]:
    """Advance one run and return its started rows in stable order."""

    engine.advance(run.pk, **({"now": now} if now is not None else {}))
    step_run_model = _workflow_model(run, "StepRun")
    with system_context(reason="workflows.testing read started"):
        return list(step_run_model.objects.filter(run=run, status=StepRunStatus.STARTED).order_by("pk"))


def execute_started(run: Any, *, now: datetime | None = None, limit: int | None = None) -> None:
    """Execute currently started step-runs synchronously in stable order."""

    step_run_model = _workflow_model(run, "StepRun")
    dispatch_model = _workflow_model(run, "WorkflowDispatch")
    with system_context(reason="workflows.testing read started"):
        rows = list(step_run_model.objects.filter(run=run, status=StepRunStatus.STARTED).order_by("pk"))
    if limit is not None:
        rows = rows[:limit]
    for row in rows:
        with system_context(reason="workflows.testing retained execute dispatch"):
            attempt = row.current_attempt
            dispatch = (
                dispatch_model.objects.filter(step_attempt=attempt).order_by("pk").first()
                if attempt is not None
                else None
            )
        if attempt is None or dispatch is None:
            raise AssertionError(f"Started StepRun {row.pk} has no retained execution dispatch.")
        engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token, now=now)


def run_to_terminal(
    run: Any,
    *,
    max_cycles: int = 80,
    stop_key: str | None = None,
) -> Any:
    """Drive a run tree until terminal, or stop before executing ``stop_key``."""

    step_run_model = _workflow_model(run, "StepRun")
    for _ in range(max_cycles):
        tree = _run_tree(run)
        for current in tree:
            current.refresh_from_db()
            if current.status not in RunStatus.TERMINAL:
                advance_once(current)

        tree = _run_tree(run)
        tree_ids = [current.pk for current in tree]
        with system_context(reason="workflows.testing inspect run tree"):
            started = list(
                step_run_model.objects.filter(
                    run_id__in=tree_ids,
                    status=StepRunStatus.STARTED,
                )
                .select_related("step", "run")
                .order_by("pk")
            )
        if stop_key is not None and any(row.step.key == stop_key for row in started):
            run.refresh_from_db()
            return run
        for current in tree:
            execute_started(current)
        _deliver_results(run)

        tree = _run_tree(run)
        for current in tree:
            current.refresh_from_db()
        if all(current.status in RunStatus.TERMINAL for current in tree):
            run.refresh_from_db()
            return run

    with system_context(reason="workflows.testing unsettled diagnostics"):
        states = list(
            step_run_model.objects.filter(run_id__in=[current.pk for current in _run_tree(run)])
            .order_by("pk")
            .values_list("run_id", "step__key", "status", "current_attempt__error")
        )
    raise AssertionError(f"Workflow run tree did not settle after {max_cycles} cycles: {states}")


def step_run_for(run: Any, key: str) -> Any:
    """Return one keyed step-run from a root's exact composed run tree."""

    step_run_model = _workflow_model(run, "StepRun")
    with system_context(reason="workflows.testing step run read"):
        return step_run_model.objects.get(
            run_id__in=[current.pk for current in _run_tree(run)],
            step__key=key,
        )
