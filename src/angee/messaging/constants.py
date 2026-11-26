"""Model-free messaging names shared by settings fragments and task registration."""

from __future__ import annotations

from datetime import timedelta

DELIVER_MESSAGE_TASK = "messaging.deliver_message"
RELEASE_MESSAGES_TASK = "messaging.release_held_messages"

SYSTEM_CHANNEL_BACKEND = "system"
"""The registry key of the system channel's backend, on which system mail is recorded."""

SYSTEM_CHANNEL_XREF = "messaging.system_channel"
"""The resource handle of the system channel, messaging's install row."""

TRANSPORT_MAILER_ALIAS = "transport"
"""The ``MAILERS`` alias of the one mailer that sends: messaging delivers every email through it."""

SYSTEM_MAILER_BACKEND = "angee.messaging.email.SystemMailer"
"""Django's default mailer wherever messaging is installed: it records each email on the system channel."""

DELIVERY_TASK_EXPIRES = 300
"""Seconds a submitted delivery task and its dispatch lease stay valid."""

MAX_DELIVERY_ATTEMPTS = 4
"""Refused provider attempts after which a delivery fails permanently."""

MAX_RECONCILE_ATTEMPTS = 5
"""Unsuccessful automatic recovery attempts before an uncertain row becomes operator-only."""

RECONCILE_BACKOFF = timedelta(minutes=1)
"""First automatic recovery delay; later attempts double it."""
