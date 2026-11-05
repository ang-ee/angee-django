"""Native choices owners for integration contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import cast

from django.db import models


def _choice_from_value[T: models.TextChoices](choice: type[T], value: object, label: str) -> T:
    raw = str(getattr(value, "value", value)).strip()
    member = choice.__members__.get(raw)
    if member is not None:
        return cast(T, member)
    try:
        return choice(raw)
    except ValueError as error:
        raise ValueError(f"Unsupported {label}: {raw}") from error


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

        return _choice_from_value(cls, value, "integration lifecycle")


class IntegrationRuntimeStatus(models.TextChoices):
    """Observed runtime health for one integration implementation."""

    OK = "ok", "OK"
    ERROR = "error", "Error"
    PENDING = "pending", "Pending"

    @classmethod
    def from_value(cls, value: object) -> IntegrationRuntimeStatus:
        """Return the member for one string or enum runtime-status value."""

        return _choice_from_value(cls, value, "integration runtime status")


class IntegrationCreateMode(StrEnum):
    """How the console starts creation of a concrete integration."""

    FORM = "form"
    CONNECT = "connect"
