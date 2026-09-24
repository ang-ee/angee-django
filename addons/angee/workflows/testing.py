"""Synchronous drivers for retained workflow runs without a live task queue."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from rebac import current_actor, system_context

from angee.base.db import get_write_alias
from angee.workflows import engine
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.states import ParentRelation, RunStatus, StepRunStatus, WaitingKind


def _workflow_model(run: Any, name: str) -> type[models.Model]:
    """Resolve one composed workflow model from the run's own app registry."""

    return run._meta.apps.get_model("workflows", name)


def _require_retained_object(value: Any, *, label: str) -> None:
    """Require a persisted harness anchor before invoking the workflow engine."""

    alias = value._state.db
    if value.pk is None or value._state.adding or alias is None:
        raise ValueError(f"workflows.testing requires a retained object; {label} uses {alias!r}.")


def _run_tree(root: Any, *, using: str) -> list[Any]:
    """Return a root and its owned-call/continuation descendants in stable order."""

    alias = using

    run_model = _workflow_model(root, "WorkflowRun")
    runs = [root]
    seen = {root.pk}
    while True:
        with system_context(reason="workflows.testing run tree"):
            children = list(
                run_model.objects.db_manager(alias)
                .filter(
                    parent_step_run__run_id__in=tuple(seen),
                    parent_relation__in=(ParentRelation.OWNED_CALL, ParentRelation.CONTINUATION),
                )
                .order_by("pk")
            )
        added = [child for child in children if child.pk not in seen]
        if not added:
            return runs
        runs.extend(added)
        seen.update(child.pk for child in added)


def _run_states(root: Any, tree: list[Any] | None = None, *, using: str) -> list[Any]:
    """Return retained run/step/attempt diagnostics for an exact run tree."""

    alias = using

    step_run_model = _workflow_model(root, "StepRun")
    tree = tree or _run_tree(root, using=alias)
    with system_context(reason="workflows.testing run diagnostics"):
        return list(
            step_run_model.objects.db_manager(alias)
            .filter(run_id__in=[current.pk for current in tree])
            .order_by("pk")
            .values_list(
                "run_id",
                "run__status",
                "step__key",
                "status",
                "current_attempt__error",
            )
        )


def _retained_run_targets(root: Any, *, using: str) -> tuple[int, ...]:
    """Return WorkflowRun artifacts currently awaited by the run tree."""

    alias = using

    step_artifact = _workflow_model(root, "StepArtifact")
    run_model = _workflow_model(root, "WorkflowRun")
    tree_ids = [current.pk for current in _run_tree(root, using=alias)]
    with system_context(reason="workflows.testing retained run targets"):
        run_type = ContentType.objects.db_manager(alias).get_for_model(run_model)
        return tuple(
            step_artifact.objects.db_manager(alias)
            .filter(
                attempt__step_run__run_id__in=tree_ids,
                attempt__step_run__current_attempt_id=F("attempt_id"),
                attempt__step_run__status=StepRunStatus.WAITING,
                attempt__step_run__waiting_kind=WaitingKind.EXTERNAL,
                target_content_type=run_type,
            )
            .order_by("target_object_id")
            .values_list("target_object_id", flat=True)
            .distinct()
        )


def _deliver_results(root: Any, *, now: datetime | None = None, using: str) -> None:
    """Consume due cancellation and result intents for the exact run tree."""

    alias = using

    dispatch_model = _workflow_model(root, "WorkflowDispatch")
    run_model = _workflow_model(root, "WorkflowRun")
    timestamp = now or timezone.now()
    tree_ids = {current.pk for current in _run_tree(root, using=alias)}
    descendant_ids = tree_ids - {root.pk}
    retained_ids = set(_retained_run_targets(root, using=alias))
    with system_context(reason="workflows.testing artifact content type"):
        run_type = ContentType.objects.db_manager(alias).get_for_model(run_model)
    delivery_scopes = (
        Q(kind=WorkflowDispatchKind.CHILD_CANCEL, run_id__in=descendant_ids)
        | Q(kind=WorkflowDispatchKind.RUN_CANCEL, run_id__in=retained_ids),
        Q(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            artifact_content_type=run_type,
            artifact_object_id__in=descendant_ids | retained_ids,
        ),
        Q(kind=WorkflowDispatchKind.RUN_SETTLE, run_id__in=tree_ids),
    )
    manager = dispatch_model.objects.db_manager(alias)
    # Each phase can retain intents consumed by a later phase in this pass.
    for scope in delivery_scopes:
        with system_context(reason="workflows.testing due result deliveries"):
            deliveries = list(
                manager.filter(scope, available_at__lte=timestamp, consumed_at__isnull=True).order_by("pk")
            )
        for dispatch in deliveries:
            manager.deliver(
                dispatch.pk,
                expected_target_id=dispatch.envelope.target_id,
                now=timestamp,
            )


def start_run(workflow: Any, *, subject: Any = None, actor: Any = None, using: str | None = None) -> Any:
    """Start a workflow run without relying on a live queue."""

    _require_retained_object(workflow, label="workflow")

    alias = get_write_alias(type(workflow), using=using, instance=workflow)

    return engine.start(workflow, subject=subject, actor=actor if actor is not None else current_actor(), using=alias)


def advance_once(run: Any, *, now: datetime | None = None, using: str | None = None) -> list[Any]:
    """Advance one run and return its started rows in stable order."""

    _require_retained_object(run, label="run")

    alias = get_write_alias(type(run), using=using, instance=run)

    engine.advance(run.pk, using=alias, **({"now": now} if now is not None else {}))
    step_run_model = _workflow_model(run, "StepRun")
    with system_context(reason="workflows.testing read started"):
        return list(
            step_run_model.objects.db_manager(alias).filter(run=run, status=StepRunStatus.STARTED).order_by("pk")
        )


def execute_started(
    run: Any,
    *,
    now: datetime | None = None,
    limit: int | None = None,
    key: str | None = None,
    using: str | None = None,
) -> None:
    """Execute currently started step-runs synchronously in stable order."""

    _require_retained_object(run, label="run")

    alias = get_write_alias(type(run), using=using, instance=run)

    if key is not None and limit is not None:
        raise TypeError("execute_started() accepts either key or limit, not both.")
    step_run_model = _workflow_model(run, "StepRun")
    dispatch_model = _workflow_model(run, "WorkflowDispatch")
    with system_context(reason="workflows.testing read started"):
        started = step_run_model.objects.db_manager(alias).filter(
            run=run,
            status=StepRunStatus.STARTED,
        )
        rows = (
            [started.select_related("step", "current_attempt").get(step__key=key)]
            if key is not None
            else list(started.select_related("current_attempt").order_by("pk"))
        )
    if limit is not None:
        rows = rows[:limit]
    for row in rows:
        with system_context(reason="workflows.testing retained execute dispatch"):
            attempt = row.current_attempt
            dispatch = (
                dispatch_model.objects.db_manager(alias).filter(step_attempt=attempt).order_by("pk").first()
                if attempt is not None
                else None
            )
        if attempt is None or dispatch is None:
            raise AssertionError(f"Started StepRun {row.pk} has no retained execution dispatch.")
        dispatch_model.objects.db_manager(alias).deliver(
            dispatch.pk,
            expected_target_id=attempt.pk,
            lease_token=attempt.lease_token,
            now=now,
        )


def run_to_terminal(
    run: Any,
    *,
    max_cycles: int = 80,
    stop_key: str | None = None,
    allow_failed: Collection[Any] = (),
    allow_canceled: Collection[Any] = (),
    using: str | None = None,
) -> Any:
    """Drive a run tree until terminal, rejecting unnamed failures and cancellations."""

    _require_retained_object(run, label="run")

    alias = get_write_alias(type(run), using=using, instance=run)

    step_run_model = _workflow_model(run, "StepRun")
    allowed_failed_ids = set(allow_failed)
    allowed_canceled_ids = set(allow_canceled)
    for _ in range(max_cycles):
        tree = _run_tree(run, using=alias)
        for current in tree:
            with system_context(reason="workflows.testing refresh run"):
                current.refresh_from_db(using=alias)
            if current.status not in RunStatus.TERMINAL:
                advance_once(current, using=alias)

        tree = _run_tree(run, using=alias)
        tree_ids = [current.pk for current in tree]
        with system_context(reason="workflows.testing inspect run tree"):
            started = list(
                step_run_model.objects.db_manager(alias)
                .filter(
                    run_id__in=tree_ids,
                    status=StepRunStatus.STARTED,
                )
                .select_related("step", "run")
                .order_by("pk")
            )
        if stop_key is not None and any(row.step.key == stop_key for row in started):
            with system_context(reason="workflows.testing refresh stopped run"):
                run.refresh_from_db(using=alias)
            return run
        for current in tree:
            execute_started(current, using=alias)
        _deliver_results(run, using=alias)

        tree = _run_tree(run, using=alias)
        for current in tree:
            with system_context(reason="workflows.testing refresh terminal run"):
                current.refresh_from_db(using=alias)
        if all(current.status in RunStatus.TERMINAL for current in tree):
            unexpected = [
                (current.pk, current.status)
                for current in tree
                if (current.status == RunStatus.FAILED and current.pk not in allowed_failed_ids)
                or (current.status == RunStatus.CANCELED and current.pk not in allowed_canceled_ids)
            ]
            if unexpected:
                raise AssertionError(
                    f"Workflow run tree settled with unexpected terminal runs "
                    f"{unexpected}: {_run_states(run, tree, using=alias)}"
                )
            with system_context(reason="workflows.testing refresh completed root"):
                run.refresh_from_db(using=alias)
            return run

    raise AssertionError(f"Workflow run tree did not settle after {max_cycles} cycles: {_run_states(run, using=alias)}")


def step_run_for(run: Any, key: str, *, using: str | None = None) -> Any:
    """Return one keyed step-run from a root's exact composed run tree."""

    _require_retained_object(run, label="run")

    alias = get_write_alias(type(run), using=using, instance=run)

    step_run_model = _workflow_model(run, "StepRun")
    with system_context(reason="workflows.testing step run read"):
        return step_run_model.objects.db_manager(alias).get(
            run_id__in=[current.pk for current in _run_tree(run, using=alias)],
            step__key=key,
        )


def owned_run(root: Any, workflow_key: str, *, subject: Any = None, using: str | None = None) -> Any:
    """Return one workflow-keyed run in the root's exact owned tree."""

    _require_retained_object(root, label="run")

    alias = get_write_alias(type(root), using=using, instance=root)

    run_model = _workflow_model(root, "WorkflowRun")
    tree_ids = [current.pk for current in _run_tree(root, using=alias)]
    filters: dict[str, Any] = {
        "pk__in": tree_ids,
        "workflow__key": workflow_key,
    }
    if subject is not None:
        subject_type = ContentType.objects.db_manager(alias).get_for_model(subject)
        filters.update(
            subject_content_type=subject_type,
            subject_object_id=subject.pk,
        )
    with system_context(reason="workflows.testing owned run read"):
        return run_model.objects.db_manager(alias).get(**filters)
