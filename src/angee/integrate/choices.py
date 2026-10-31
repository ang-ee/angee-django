"""Native choices owners for integration contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import cast

from django.db import models


class AccountStatus(models.TextChoices):
    """Connection lifecycle for a linked external account.

    Pure connection health — does the account's credential still work. The
    integration implementation health lives on ``Integration``, not here.
    """

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class CredentialStatus(models.TextChoices):
    """Lifecycle state for per-user credential material."""

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class IntegrationLifecycle(models.TextChoices):
    """Durable connection lifecycle shared by every integration."""

    DISCONNECTED = "disconnected", "Disconnected"
    CONNECTED = "connected", "Connected"
    PAUSED = "paused", "Paused"

    @classmethod
    def from_value(cls, value: object) -> IntegrationLifecycle:
        """Return the member for one string or enum integration-lifecycle value."""

        raw = str(getattr(value, "value", value)).strip()
        member = cls.__members__.get(raw)
        if member is not None:
            return cast(IntegrationLifecycle, member)
        try:
            return cast(IntegrationLifecycle, cls(raw))
        except ValueError as error:
            raise ValueError(f"Unsupported integration lifecycle: {raw}") from error


class IntegrationRuntimeStatus(models.TextChoices):
    """Observed runtime health for one integration implementation."""

    OK = "ok", "OK"
    ERROR = "error", "Error"
    PENDING = "pending", "Pending"

    @classmethod
    def from_value(cls, value: object) -> IntegrationRuntimeStatus:
        """Return the member for one string or enum runtime-status value."""

        raw = str(getattr(value, "value", value)).strip()
        member = cls.__members__.get(raw)
        if member is not None:
            return cast(IntegrationRuntimeStatus, member)
        try:
            return cast(IntegrationRuntimeStatus, cls(raw))
        except ValueError as error:
            raise ValueError(f"Unsupported integration runtime status: {raw}") from error


class IntegrationCreateMode(StrEnum):
    """How the console starts creation of a concrete integration."""

    FORM = "form"
    CONNECT = "connect"
