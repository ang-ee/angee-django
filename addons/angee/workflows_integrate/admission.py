"""Retain one workflow invocation for an integration cadence occurrence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from rebac import system_context, to_subject_ref

from angee.base.db import get_write_alias, related_on
from angee.base.identity import public_id_for
from angee.base.permissions import require_authorization_database
from angee.integrate.models import Bridge
from angee.integrate.sync import BridgeProgressReporter, SyncDispatch
from angee.workflows.attempts import JsonPresence
from angee.workflows.states import RunStatus


def _active_owner(bridge: Any, actor: Any, *, using: str) -> Any:
    owner = related_on(bridge, "owner", using=using)
    if owner is None or not owner.is_active:
        raise PermissionDenied("Bridge cycles require an active Integration owner.")
    if actor is None or to_subject_ref(actor) != to_subject_ref(owner):
        raise PermissionDenied("Bridge cycles must run as the Integration owner.")
    return owner


def admit_bridge_cycle(
    bridge: Bridge,
    *,
    workflow: Any,
    occurrence_key: str,
    actor: Any,
    input: JsonPresence,
    available_at: datetime | None = None,
    using: str | None = None,
) -> Any:
    """Exactly retain a cycle; cadence identity is only the run's dedup key.

    ``input`` is the native workflow JsonPresence contract. The Integration's
    active owner is the actor; callers cannot substitute the workflow author.
    The manager owns publication selection, frozen-input validation and exact
    duplicate matching. Its validate_new hook serializes different lineages on
    the Bridge row before refusing any other active cycle.
    """

    using = get_write_alias(type(bridge), using=using, instance=bridge)
    require_authorization_database(using, operation="Bridge cycle admission", error_field="using")
    if bridge.pk is None or not isinstance(occurrence_key, str) or not occurrence_key:
        raise ValidationError({"occurrence_key": "A saved Bridge and cadence occurrence are required."})
    bridge._state.db = using
    run_model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows_integrate.admit"), transaction.atomic(using=using):
        current = type(bridge).objects.db_manager(using).get(pk=bridge.pk)
        owner = _active_owner(current, actor, using=using)
        content_type = ContentType.objects.db_manager(using).get_for_model(bridge, for_concrete_model=False)
        dedup_key = f"bridge-sync:{content_type.pk}:{bridge.pk}:{occurrence_key}"
        new_bridge = None

        def validate_new() -> None:
            nonlocal new_bridge
            new_bridge = type(bridge).objects.db_manager(using).lock_if_supported().get(pk=bridge.pk)
            _active_owner(new_bridge, owner, using=using)
            active = run_model.objects.db_manager(using).for_subject(new_bridge).exclude(status__in=RunStatus.TERMINAL)
            if active.exclude(dedup_key=dedup_key).exists():
                raise ValidationError({"bridge": "This Bridge already has an active cycle."})

        run = run_model.objects.db_manager(using).start(
            workflow,
            subject=bridge,
            actor=owner,
            dedup_key=dedup_key,
            input=input,
            available_at=available_at,
            validate_new=validate_new,
            using=using,
        )
        if new_bridge is not None:
            now = timezone.now()
            new_bridge.mark_sync_started(now=now, using=using)
            new_bridge.next_sync_at = None
            new_bridge.save(update_fields=["next_sync_at", "updated_at"], using=using)
            details = dict(new_bridge.sync_progress.get("details", {}))
            details["run"] = public_id_for(type(run), run.pk)
            BridgeProgressReporter(new_bridge, using=using).report(new_bridge.SyncStage.SYNCING, details=details)
            bridge.sync_stage = new_bridge.sync_stage
            bridge.sync_progress = new_bridge.sync_progress
            bridge.next_sync_at = None
        return run


def dispatch_bridge_cycle(bridge: Bridge, *, using: str | None = None) -> SyncDispatch:
    """Admit a declared workflow key at its current publication from Bridge.sync."""

    using = get_write_alias(type(bridge), using=using, instance=bridge)
    require_authorization_database(using, operation="Bridge cycle admission", error_field="using")
    bridge._state.db = using
    occurrence_key = bridge.sync_progress.get("queued_at")
    if not occurrence_key:
        raise ValidationError({"occurrence_key": "Queue the Bridge before dispatching a cycle."})
    workflow_model = apps.get_model("workflows", "Workflow")
    with system_context(reason="workflows_integrate.dispatch"):
        workflow = (
            workflow_model.objects.db_manager(using)
            .filter(published_from__isnull=True, key=bridge.sync_workflow_key)
            .first()
        )
        if workflow is None:
            raise ValidationError({"sync_workflow_key": "The declared workflow key does not exist."})
        owner = related_on(bridge, "owner", using=using)
        admit_bridge_cycle(
            bridge,
            workflow=workflow,
            occurrence_key=occurrence_key,
            actor=owner,
            input=JsonPresence(present=True, value=bridge.sync_workflow_input()),
            using=using,
        )
    return SyncDispatch.DISPATCHED
