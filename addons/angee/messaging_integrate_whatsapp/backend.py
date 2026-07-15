"""WhatsApp channel backend: live-session dispatch over the bridges seam.

The session itself — socket, QR pairing, ingest — runs in a long-lived task on
the dedicated ``whatsapp`` Celery queue (:mod:`.tasks` / :mod:`.session`); this
backend is the Channel-facing seam only. ``fetch_messages`` is empty because the
bridge is push-mode: the session ingests out of band and ``next_sync_at`` is
never populated, so the poll scheduler ignores the channel (a manual
``syncIntegration`` is a cheap no-op). ``start_live`` dispatches the session
task; stopping is cooperative through the base's persisted desired-state, so
``stop_live`` needs no vendor action beyond the inherited no-op.
"""

from __future__ import annotations

from angee.messaging.backends import ChannelBackend, ParsedMessage
from angee.messaging_integrate_whatsapp.constants import (
    RECONCILER_INTERVAL,
    RUN_SESSION_TASK,
    SESSION_QUEUE,
)
from angee.tasks.enqueue import enqueue_task

SESSION_START_EXPIRES = RECONCILER_INTERVAL
"""Discard an unconsumed session start after one reconciler tick — the beat
re-enqueues while the channel stays live-desired, so a saturated or absent
worker never accumulates a stale backlog."""


class WhatsAppChannelBackend(ChannelBackend):
    """Channel backend for a linked WhatsApp account — one live session per channel."""

    key = "whatsapp"
    label = "WhatsApp"
    icon = "message-circle"

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return nothing — a push bridge ingests from its live session, never a poll."""

        return []

    def start_live(self) -> None:
        """Dispatch this channel's live session to the dedicated queue.

        Safe to repeat: the session task's non-blocking advisory-lock acquire
        makes a duplicate start exit immediately, and ``expires`` keeps an
        undelivered start from outliving the next reconciler tick.
        """

        enqueue_task(
            RUN_SESSION_TASK,
            kwargs={"channel_id": self.bridge.pk},
            queue=SESSION_QUEUE,
            expires=SESSION_START_EXPIRES,
        )
