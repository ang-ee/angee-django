"""Celery task wrappers for integrate bridge schedulers and live sessions."""

from __future__ import annotations

import logging
import threading
from typing import Any

from celery import shared_task
from celery.signals import worker_shutting_down
from django.apps import apps
from rebac import database_context, system_context
from rebac.database import write_alias

from angee.base.locks import task_locks_are_cross_process
from angee.integrate import scheduler
from angee.integrate.constants import DISCOVERY_TASK, DISCOVERY_TASK_EXPIRES, ENSURE_SESSIONS_TASK, RUN_SESSION_TASK
from angee.integrate.models import Bridge
from angee.integrate.registry import models_with
from angee.integrate.session_runner import run_bridge_session_job
from angee.integrate.sync_runner import run_bridge_sync_job

logger = logging.getLogger(__name__)

_shutdown = threading.Event()
"""Set on worker shutdown so every live session exits within one wake."""


@shared_task(name=DISCOVERY_TASK, expires=DISCOVERY_TASK_EXPIRES)
def discover_connection(
    integration_pk: int, credential_pk: int | None, generation: int, *, using: str | None = None
) -> dict[str, Any]:
    """Dispatch discovery on the producer alias, or the model write router when omitted."""

    model = apps.get_model("integrate", "Integration")
    using = write_alias(model, using=using)
    with database_context(using), system_context(reason="integrate.connection.discovery"):
        integration = model.objects.db_manager(using).filter(pk=integration_pk).first()
        if integration is None:
            return {"ok": False, "reason": "missing"}
        return integration.run_discovery(credential_pk=credential_pk, generation=generation)


@worker_shutting_down.connect
def _flag_shutdown(**_kwargs: Any) -> None:
    _shutdown.set()


@shared_task(
    name="integrate.sync_bridge_now",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def sync_bridge_now(
    model_label: str, pk: int, timestamp: str | None = None, *, using: str | None = None
) -> dict[str, Any]:
    """Run queued sync on its producer; legacy payloads use model write routing."""

    return run_bridge_sync_job(model_label, pk, timestamp, require_queue_token=True, using=using)


@shared_task(
    name="integrate.sync_due_bridges",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def sync_due_bridges(timestamp: int | None = None) -> None:
    """Queue every bridge row whose ``next_sync_at`` is due."""

    del timestamp
    for step in (scheduler.enqueue_pending_discoveries, scheduler.enqueue_due_bridges):
        try:
            step()
        except Exception:
            logger.exception("Integration scheduler step %s failed.", step.__name__)


@shared_task(name=RUN_SESSION_TASK, time_limit=None, soft_time_limit=None)
def run_bridge_session(model_label: str, pk: Any, *, using: str | None = None) -> dict[str, Any]:
    """Run the selected producer session with its implementation's isolation policy."""

    return run_bridge_session_job(model_label, pk, stop_event=_shutdown, using=using)


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
    with system_context(reason="integrate.ensure_bridge_sessions"):
        for model in models_with(base=Bridge):
            using = write_alias(model)
            cross_process = task_locks_are_cross_process(using=using)
            for bridge in model._default_manager.db_manager(using).live_session_candidates():
                queue = bridge.reconcile_live_session(cross_process=cross_process)
                if queue is None:
                    continue
                dispatched += 1
                if queue:
                    starved_by_queue[queue] = starved_by_queue.get(queue, 0) + 1
    for queue, count in sorted(starved_by_queue.items()):
        logger.warning(
            "%s live-desired bridge(s) show an active stage with no running session - "
            "is the dedicated '%s' queue worker up and unsaturated?",
            count,
            queue,
        )
    return {"ok": True, "dispatched": dispatched}
