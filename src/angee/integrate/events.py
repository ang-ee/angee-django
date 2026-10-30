"""Integration event kinds emitted through outbound webhooks."""

from __future__ import annotations

from django.db import models


class EventKind(models.TextChoices):
    """Stable event names for integration webhook delivery."""

    BRIDGE_SYNCED = "bridge.synced"
    BRIDGE_ERRORED = "bridge.errored"
    BRIDGE_DISABLED = "bridge.disabled"
    CAPABILITY_ERRORED = "capability.errored"
    ACCOUNT_EXPIRED = "account.expired"
    ACCOUNT_REVOKED = "account.revoked"
