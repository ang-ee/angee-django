"""Celery task wrappers for integrate bridge schedulers and live sessions."""

from __future__ import annotations

import logging
import threading
from typing import Any

from celery import shared_task
from celery.signals import worker_shutting_down
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from rebac import system_context

from angee.integrate import scheduler
from angee.integrate.constants import ENSURE_SESSIONS_TASK, RUN_SESSION_TASK
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import PairingState, SessionLoggedOut
from angee.integrate.locks import bridge_advisory_lock, bridge_is_locked
from angee.integrate.models import Bridge, IntegrationRuntimeStatus
from angee.integrate.registry import bridge_models
from angee.integrate.sync import bridge_progress_context
from angee.integrate.sync_runner import run_bridge_sync_job
from angee.tasks.locks import task_locks_are_cross_process

logger = logging.getLogger(__name__)

_shutdown = threading.Event()
"""Set on worker shutdown so every live session exits within one wake."""


@worker_shutting_down.connect
def _flag_shutdown(**_kwargs: Any) -> None:
    _shutdown.set()


@shared_task(
    name="integrate.sync_bridge_now",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def sync_bridge_now(model_label: str, pk: int, timestamp: str | None = None) -> dict[str, Any]:
    """Run one queued bridge sync task."""

    return run_bridge_sync_job(model_label, pk, timestamp, require_queue_token=True)


@shared_task(
    name="integrate.sync_due_bridges",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def sync_due_bridges(timestamp: int | None = None) -> None:
    """Queue every bridge row whose ``next_sync_at`` is due."""

    del timestamp
    scheduler.enqueue_due_bridges()


@shared_task(name=RUN_SESSION_TASK, time_limit=None, soft_time_limit=None)
def run_bridge_session(model_label: str, pk: Any) -> dict[str, Any]:
    """Run one bridge's live session for the life of its vendor connection."""

    with system_context(reason="integrate.run_bridge_session"):
        bridge = _bridge(model_label, pk)
        if bridge is None:
            return {"ok": True, "skipped": True, "reason": "not-a-bridge"}
        impl = bridge.live_impl
        if not isinstance(impl, LiveBridgeImpl):
            return {"ok": True, "skipped": True, "reason": "not-live-capable"}
        if bridge.subscription_state.get("desired") != bridge.LiveState.LIVE:
            return {"ok": True, "skipped": True, "reason": "not-live-desired"}
        if type(bridge).Lifecycle.from_value(bridge.lifecycle) is not type(bridge).Lifecycle.CONNECTED:
            return {"ok": True, "skipped": True, "reason": "not-connected"}
        with bridge_advisory_lock(bridge) as acquired:
            if not acquired:
                return {"ok": True, "skipped": True, "reason": "session-already-running"}
            with bridge_progress_context(bridge) as reporter:
                session = impl.session_class_resolved()(bridge, reporter=reporter, stop_event=_shutdown)
                try:
                    state = session.run()
                except SessionLoggedOut as error:
                    _record_logged_out(bridge, error, session=session)
                    return {"ok": False, "logged_out": True}
                if state == PairingState.DUPLICATE_ACCOUNT:
                    _record_duplicate_account(bridge, session=session)
                    return {"ok": False, "duplicate_account": True}
                if session.outcome_error is not None:
                    return {"ok": False, "session_error": True, "state": state}
                reporter.report(bridge.SyncStage.IDLE, details={"pairing": {"state": state}})
        return {"ok": True, "state": state, "items": session.landed}


def _record_logged_out(bridge: Any, error: Exception, *, session: Any) -> None:
    """Record a logout as runtime failure, then release desire/account and store.

    The lifecycle is untouched: the operator declared this bridge connected and
    a handshake outcome does not revoke that. ``record_sync_error`` puts the
    outcome on runtime health and the failed sync stage, which also keeps the
    reconciler from redispatching a session that can only fail again.

    Error bookkeeping must run first: while desire still reads live, the
    bridge's next-sync policy keeps it out of the poll loop, so the failed stage
    is not masked by a later no-op poll. Only then is desire cleared under its
    row-locked merge.
    """

    impl = _require_live_impl(bridge)
    bridge.record_sync_error(error, now=timezone.now())
    impl.release_account(desired=bridge.LiveState.STOPPED)
    session.discard_store()


def _record_duplicate_account(bridge: Any, *, session: Any) -> None:
    """Record a rejected duplicate as runtime failure and release the void claim.

    Being told another bridge owns this account is a handshake outcome, not the
    operator changing intent, so runtime health records it and lifecycle stands.
    The store is discarded only if this session created the pairing material it
    would delete.
    """

    impl = _require_live_impl(bridge)
    error = session.duplicate_error or impl.duplicate_account_error()
    bridge.record_sync_error(error, now=timezone.now())
    impl.release_account(desired=bridge.LiveState.STOPPED)
    session.discard_new_store()


@shared_task(name=ENSURE_SESSIONS_TASK)
def ensure_bridge_sessions(timestamp: int | None = None) -> dict[str, Any]:
    """Reconcile every healthy connected live-capable bridge to a running session.

    Selection starts from lifecycle, the operator's declared intent, and runtime
    health gates redispatch after a known failed handshake. The live desire is
    reconciled only when the two axes disagree: ``start_live`` writes and
    publishes without a dirty check, so calling it on every settled tick would
    broadcast a no-op change per healthy bridge per minute and could force
    ``desired=LIVE`` back onto a row something else had just stopped.
    """

    del timestamp
    dispatched = 0
    starved_by_queue: dict[str, int] = {}
    cross_process = task_locks_are_cross_process()
    with system_context(reason="integrate.ensure_bridge_sessions"):
        for model in bridge_models(Bridge):
            field = model._meta.get_field(model.live_impl_field)
            live_keys: list[str] = []
            for key in field.registered_keys():
                try:
                    impl_class = field.resolve_class(key)
                except AttributeError, ImportError, ImproperlyConfigured:
                    logger.exception(
                        "Skipping unresolvable %s live implementation key %r.",
                        model._meta.label_lower,
                        key,
                    )
                    continue
                if issubclass(impl_class, LiveBridgeImpl):
                    live_keys.append(key)
            if not live_keys:
                continue
            bridges = model._default_manager.filter(
                **{f"{model.live_impl_field}__in": live_keys},
                lifecycle=str(model.Lifecycle.CONNECTED),
                runtime_status=str(IntegrationRuntimeStatus.OK),
            ).order_by("pk")
            for bridge in bridges:
                impl = bridge.live_impl
                if not isinstance(impl, LiveBridgeImpl):
                    continue
                if cross_process and bridge_is_locked(bridge):
                    continue
                if bridge.subscription_state.get("desired") != bridge.LiveState.LIVE:
                    bridge.start_live()
                else:
                    impl.start_live()
                dispatched += 1
                if cross_process and bridge.sync_stage == bridge.SyncStage.SYNCING:
                    queue = impl.session_queue
                    starved_by_queue[queue] = starved_by_queue.get(queue, 0) + 1
    for queue, count in sorted(starved_by_queue.items()):
        logger.warning(
            "%s live-desired bridge(s) show a syncing stage with no running session - "
            "is the dedicated '%s' queue worker up and unsaturated?",
            count,
            queue,
        )
    return {"ok": True, "dispatched": dispatched}


def _bridge(model_label: str, pk: Any) -> Any | None:
    """Return one bridge row for a live session task, or ``None``."""

    try:
        app_label, model_name = str(model_label).split(".", 1)
    except ValueError:
        return None
    model = apps.get_model(app_label, model_name)
    if not issubclass(model, Bridge):
        return None
    return model._default_manager.filter(pk=pk).first()


def _require_live_impl(bridge: Any) -> LiveBridgeImpl:
    """Return the bridge's live implementation after an earlier live gate."""

    impl = bridge.live_impl
    if not isinstance(impl, LiveBridgeImpl):
        raise TypeError("Bridge no longer resolves to a live implementation.")
    return impl
