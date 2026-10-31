"""Model-free messaging names shared by settings fragments and task registration."""

from __future__ import annotations

from datetime import timedelta

DELIVER_MESSAGE_TASK = "messaging.deliver_message"
RELEASE_MESSAGES_TASK = "messaging.release_held_messages"

DELIVERY_TASK_EXPIRES = 300
MAX_DELIVERY_ATTEMPTS = 4
MAX_RECONCILE_ATTEMPTS = 5
RECONCILE_BACKOFF = timedelta(minutes=1)
