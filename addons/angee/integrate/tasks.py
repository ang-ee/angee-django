"""Celery task wrappers for integrate bridge schedulers and live sessions."""

from __future__ import annotations

import logging
import threading
from typing import Any

from celery import shared_task
from celery.signals import worker_shutting_down
from django.core.exceptions import ImproperlyConfigured
from rebac import system_context

from angee.integrate import scheduler
from angee.integrate.constants import ENSURE_SESSIONS_TASK, RUN_SESSION_TASK
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.locks import bridge_is_locked, bridge_session_is_hosted
from angee.integrate.models import Bridge, IntegrationRuntimeStatus
from angee.integrate.registry import bridge_models
from angee.integrate.session_runner import run_bridge_session_job
from angee.integrate.sync_runner import run_bridge_sync_job
from angee.jobs.locks import task_locks_are_cross_process

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
    """Run one bridge session using its implementation's isolation policy."""

    return run_bridge_session_job(model_label, pk, stop_event=_shutdown)


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
            field = model.live_implementation_field()
            if field is None:
                continue
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
                **{f"{field.name}__in": live_keys},
                lifecycle=str(model.Lifecycle.CONNECTED),
                runtime_status=str(IntegrationRuntimeStatus.OK),
            ).order_by("pk")
            for bridge in bridges:
                impl = bridge.live_impl
                if not isinstance(impl, LiveBridgeImpl):
                    continue
                if cross_process and (bridge_is_locked(bridge) or bridge_session_is_hosted(bridge)):
                    continue
                if bridge.subscription_state.get("desired") != bridge.LiveState.LIVE:
                    bridge.start_live()
                else:
                    impl.start_live()
                dispatched += 1
                if cross_process and bridge.sync_stage in bridge.LIVE_SYNC_STAGES:
                    queue = impl.session_queue
                    starved_by_queue[queue] = starved_by_queue.get(queue, 0) + 1
    for queue, count in sorted(starved_by_queue.items()):
        logger.warning(
            "%s live-desired bridge(s) show an active stage with no running session - "
            "is the dedicated '%s' queue worker up and unsaturated?",
            count,
            queue,
        )
    return {"ok": True, "dispatched": dispatched}
