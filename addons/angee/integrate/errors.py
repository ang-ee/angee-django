"""The integration failure whose message is safe to show an operator.

Integration telemetry (``Bridge.sync_error``, ``Integration.last_error``) and
console action outcomes must never echo a raw exception — vendor SDKs put
tokens, hostnames, and request bodies in their messages. This base is the one
opt-in: an integration-owned exception that subclasses it declares its message
bounded and operator-safe, so the failure the operator needs ("login failed
for ada@example.com") reaches the console instead of the generic
"Integration operation failed." ``OAuthFlowError`` is the code-keyed member;
a backend raises a plain subclass with the message it composed itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import DataError

INTEGRATION_FAILURE_MESSAGE = "Integration operation failed."
"""The bounded message every unclassified integration failure projects to."""


class IntegrationError(Exception):
    """An integration failure carrying an operator-safe ``public_message``.

    Subclasses compose the message from facts they own (the login name, the
    host, the server's refusal) and never from raw vendor payloads.
    """

    @property
    def public_message(self) -> str:
        """Return the bounded text safe to persist and show to operators."""

        return str(self) or INTEGRATION_FAILURE_MESSAGE


@dataclass(frozen=True, slots=True)
class IntegrationFailure:
    """An integration-owned, safe failure message for persisted telemetry."""

    message: str


def _safe_integration_failure(error: Exception) -> IntegrationFailure:
    """Project safe integration refusals; keep unclassified vendor text private."""

    if isinstance(error, IntegrationError):
        return IntegrationFailure(error.public_message)
    if isinstance(error, ValidationError):
        return IntegrationFailure("Integration configuration is invalid.")
    if isinstance(error, DataError):
        return IntegrationFailure("Database rejected invalid or oversized record data.")
    return IntegrationFailure(INTEGRATION_FAILURE_MESSAGE)
