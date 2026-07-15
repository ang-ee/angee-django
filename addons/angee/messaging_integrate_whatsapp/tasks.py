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

``whatsapp.ensure_sessions`` is the beat reconciler on the default queue: it
reconciles every healthy connected channel to a running session, restarting one
with ``expires`` of one tick, so a crashed worker resumes within a minute and a
saturated or absent worker never accumulates a backlog. The cross-process lock
probe is an optimization only — on the process-local lock floor the task's own
acquire stays the gate.

A session runs for a CONNECTED channel only. The lifecycle is the operator's
declared connection intent and it is reachable from outside this addon — a
channel *is* an ``integrate.Integration`` row, so the generic Integration
actions move it by its ``int_`` sqid — so both other lifecycles stop the session
here, in ``run_session``, and in the session's own wake loop alike. The
reconciler closes the same loop in the CONNECTED direction, so the lifecycle is
the one axis an operator has to get right; the live desire follows it.

Neither teardown path writes the lifecycle back. A logout or a rejected
duplicate is how far the handshake got, not what the operator asked for, so it
lands on ``runtime_status``/``sync_progress`` and stops the session through the
live desire the same call clears.
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
from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus
from angee.integrate.sync import bridge_progress_context
from angee.messaging_integrate_whatsapp.backend import WhatsAppChannelBackend
from angee.messaging_integrate_whatsapp.client import (
    DuplicateAccountRejected,
    PairingState,
    SessionLoggedOut,
)
from angee.messaging_integrate_whatsapp.constants import (
    ENSURE_SESSIONS_TASK,
    RUN_SESSION_TASK,
    SESSION_QUEUE,
)
from angee.messaging_integrate_whatsapp.session import WhatsAppSession
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
        if IntegrationLifecycle.from_value(channel.lifecycle) is not IntegrationLifecycle.CONNECTED:
            return {"ok": True, "skipped": True, "reason": "not-connected"}
        with bridge_advisory_lock(channel) as acquired:
            if not acquired:
                return {"ok": True, "skipped": True, "reason": "session-already-running"}
            with bridge_progress_context(channel) as reporter:
                session = WhatsAppSession(channel, reporter=reporter, stop_event=_shutdown)
                try:
                    state = session.run()
                except SessionLoggedOut as error:
                    _record_logged_out(channel, error, session=session)
                    return {"ok": False, "logged_out": True}
                if state == PairingState.DUPLICATE_ACCOUNT:
                    _record_duplicate_account(channel, session=session)
                    return {"ok": False, "duplicate_account": True}
                reporter.report(channel.SyncStage.IDLE, details={"pairing": {"state": state}})
        return {"ok": True, "state": state, "items": session.landed}


def _record_logged_out(channel: Any, error: Exception, *, session: WhatsAppSession) -> None:
    """Record a phone-side logout as a runtime failure and release the void claim.

    The lifecycle is untouched: the operator declared this channel connected and
    a handshake outcome does not revoke that. ``record_sync_error`` puts the
    outcome where it belongs (``runtime_status`` ERROR + the FAILED stage), which
    is also what keeps ``ensure_sessions`` from re-dispatching a session that can
    only fail again; the store is void, so it goes.
    """

    # Error bookkeeping first: while the desire still reads live,
    # ``_next_sync_at`` keeps the channel out of the poll loop, so the FAILED
    # stage isn't masked by a later no-op poll. Only then clear the desire —
    # merged under a row lock so it never clobbers a concurrent operator write.
    channel.record_sync_error(error, now=timezone.now())
    channel.backend.release_account(desired=channel.LiveState.STOPPED)
    session.discard_store()


def _record_duplicate_account(channel: Any, *, session: WhatsAppSession) -> None:
    """Record a rejected duplicate as a runtime failure and release the void claim.

    Symmetric with :func:`_record_logged_out`, and for the same reason: being
    told "another channel owns this account" is a handshake outcome, not the
    operator changing their mind, so it lands on ``runtime_status`` and the
    lifecycle stands. The store is discarded only if this session created the
    pairing material it would delete
    (:meth:`~.session.WhatsAppSession.discard_new_store`).
    """

    channel.record_sync_error(
        DuplicateAccountRejected("Another channel already owns this WhatsApp account."),
        now=timezone.now(),
    )
    channel.backend.release_account(desired=channel.LiveState.STOPPED)
    session.discard_new_store()


@shared_task(name=ENSURE_SESSIONS_TASK)
def ensure_sessions() -> dict[str, Any]:
    """Reconcile every healthy connected channel to a running session (beat, 60s).

    Selects on the lifecycle — the operator's declared intent — and reconciles
    the live desire to it through ``start_live``, rather than selecting on the
    desire and trusting it to already agree. That closes the CONNECTED direction:
    a channel connected through the *generic* ``markIntegrationConnected`` (every
    Integration child answers to it by its ``int_`` sqid, without this addon in
    the call path) declares lifecycle only, and nothing else would ever give it a
    session.

    ``runtime_status`` is the gate in the other direction. A channel whose last
    handshake ended in a logout or a rejected duplicate keeps its CONNECTED
    lifecycle — the operator still wants it — but reconciling it would redispatch
    a session that can only fail the same way. It waits for an operator repair to
    clear the error, and every repair verb does: ``resumeWhatsappPairing`` (which
    ``resetWhatsappPairing`` ends in) reports OK itself, and the generic
    ``markIntegrationConnected`` clears it through ``connect()``.

    The live desire is reconciled only when the two axes disagree. ``start_live``
    has no dirty check — it takes a row lock, writes, and publishes
    ``channelChanged`` — so writing it every tick would broadcast a no-op edit per
    healthy channel per minute, and would force ``desired=LIVE`` back onto a row
    something else had just stopped.
    """

    model = apps.get_model("messaging", "Channel")
    dispatched = 0
    starved = 0
    with system_context(reason="whatsapp.ensure_sessions"):
        channels = model._default_manager.filter(
            backend_class=WhatsAppChannelBackend.key,
            lifecycle=str(IntegrationLifecycle.CONNECTED),
            runtime_status=str(IntegrationRuntimeStatus.OK),
        )
        cross_process = task_locks_are_cross_process()
        for channel in channels:
            if cross_process and bridge_is_locked(channel):
                continue
            if channel.subscription_state.get("desired") != channel.LiveState.LIVE:
                channel.start_live()
            else:
                channel.backend.start_live()
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
