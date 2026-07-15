"""Celery tasks — the live WhatsApp session runner and its reconciler.

``whatsapp.run_session`` is a long-lived task: it holds one channel's bridge
advisory lock for the life of the connection, so the non-blocking acquire IS
the duplicate gate — a second start for the same channel exits immediately.
The operative protection for a task that outlives the poll cadence is queue
isolation: the ``whatsapp`` queue is routed to a **threads pool** (the stack
template renders ``--pool threads`` for every dedicated queue worker), and the
threads pool enforces no time limits at all. ``time_limit=None`` on the task
is not itself a safeguard — Celery inherits the global limit rather than
dropping it — so this must never run on a prefork worker; the threads-pool
routing is the fact that makes it safe.

``whatsapp.ensure_sessions`` is the beat reconciler on the default queue: any
live-desired channel whose session is not running gets a fresh start with
``expires`` of one tick, so a crashed worker resumes within a minute and a
saturated or absent worker never accumulates a backlog. The cross-process lock
probe is an optimization only — on the process-local lock floor the task's own
acquire stays the gate.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from celery import shared_task
from celery.signals import worker_shutting_down
from django.apps import apps
from django.utils import timezone
from rebac import system_context

from angee.integrate.locks import bridge_advisory_lock, bridge_is_locked
from angee.integrate.sync import bridge_progress_context
from angee.messaging_integrate_whatsapp.backend import (
    SESSION_START_EXPIRES,
    WhatsAppChannelBackend,
)
from angee.messaging_integrate_whatsapp.client import SessionLoggedOut
from angee.messaging_integrate_whatsapp.constants import (
    ENSURE_SESSIONS_TASK,
    RUN_SESSION_TASK,
    SESSION_QUEUE,
)
from angee.messaging_integrate_whatsapp.session import WhatsAppSession
from angee.tasks.enqueue import enqueue_task
from angee.tasks.locks import task_locks_are_cross_process

logger = logging.getLogger(__name__)

_shutdown = threading.Event()
"""Set on ``worker_shutting_down`` so every live session exits within one wake
and a warm SIGTERM never wedges behind the threads pool's blocking join."""


@worker_shutting_down.connect
def _flag_shutdown(**_kwargs: Any) -> None:
    _shutdown.set()


def _channel(channel_id: Any) -> Any | None:
    """Return the live-capable channel row for a session task, or ``None``."""

    model = apps.get_model("messaging", "Channel")
    channel = model._default_manager.filter(pk=channel_id).first()
    if channel is None or channel.backend_class != WhatsAppChannelBackend.key:
        return None
    return channel


@shared_task(name=RUN_SESSION_TASK, time_limit=None, soft_time_limit=None)
def run_session(channel_id: Any) -> dict[str, Any]:
    """Run one channel's live session for the life of its connection."""

    with system_context(reason="whatsapp.run_session"):
        channel = _channel(channel_id)
        if channel is None:
            return {"ok": True, "skipped": True, "reason": "not-a-whatsapp-channel"}
        if channel.subscription_state.get("desired") != channel.LiveState.LIVE:
            return {"ok": True, "skipped": True, "reason": "not-live-desired"}
        with bridge_advisory_lock(channel) as acquired:
            if not acquired:
                return {"ok": True, "skipped": True, "reason": "session-already-running"}
            with bridge_progress_context(channel) as reporter:
                session = WhatsAppSession(channel, reporter=reporter, stop_event=_shutdown)
                try:
                    state = session.run()
                except SessionLoggedOut as error:
                    _record_logged_out(channel, error)
                    return {"ok": False, "logged_out": True}
                reporter.report(channel.SyncStage.IDLE, details={"pairing": {"state": state}})
        return {"ok": True, "state": state, "items": session.landed}


def _record_logged_out(channel: Any, error: Exception) -> None:
    """Persist the unlinked state: an explicit pairing reset is the only way back.

    Clearing the live desire keeps the reconciler from re-dispatching a session
    that can only fail again; ``record_sync_error`` owns the error bookkeeping
    (and leaves a live channel unscheduled through ``_next_sync_at``).
    """

    # Error bookkeeping first: while the desire still reads live,
    # ``_next_sync_at`` keeps the channel out of the poll loop, so the FAILED
    # stage isn't masked by a later no-op poll. Only then clear the desire —
    # merged under a row lock so it never clobbers a concurrent operator write.
    channel.record_sync_error(error, now=timezone.now())
    channel.merge_subscription_state(desired=channel.LiveState.STOPPED)


@shared_task(name=ENSURE_SESSIONS_TASK)
def ensure_sessions() -> dict[str, Any]:
    """Restart any live-desired channel whose session is not running (beat, 60s)."""

    model = apps.get_model("messaging", "Channel")
    dispatched = 0
    starved = 0
    with system_context(reason="whatsapp.ensure_sessions"):
        channels = model._default_manager.filter(
            backend_class=WhatsAppChannelBackend.key,
            subscription_state__desired=model.LiveState.LIVE.value,
        )
        cross_process = task_locks_are_cross_process()
        for channel in channels:
            if cross_process and bridge_is_locked(channel):
                continue
            enqueue_task(
                RUN_SESSION_TASK,
                kwargs={"channel_id": channel.pk},
                queue=SESSION_QUEUE,
                expires=SESSION_START_EXPIRES,
            )
            dispatched += 1
            # A syncing stage with no held lock (probe-visible backends only)
            # means a session died or never got a pool slot — make it visible.
            if cross_process and channel.sync_stage == channel.SyncStage.SYNCING:
                starved += 1
    if starved:
        logger.warning(
            "%s live-desired WhatsApp channel(s) show a syncing stage with no running session — "
            "is the dedicated '%s' queue worker up and unsaturated?",
            starved,
            SESSION_QUEUE,
        )
    return {"ok": True, "dispatched": dispatched}
