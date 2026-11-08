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

import errno
import socket
import ssl
from dataclasses import dataclass
from datetime import timedelta

import httpx2
from django.core.exceptions import ValidationError
from django.db import DataError, OperationalError

INTEGRATION_FAILURE_MESSAGE = "Integration operation failed."
"""The bounded message every unclassified integration failure projects to."""

BACKOFF_EXPONENT_CAP = 11
"""Doubling stops after 2**11 minutes so the delay arithmetic stays bounded."""

RETRYABLE_ERRNOS = frozenset({
    errno.EAGAIN, errno.ENOMEM, errno.ENOBUFS, errno.EMFILE, errno.ENFILE,
    errno.ENETDOWN, errno.ENETUNREACH, errno.ENETRESET, errno.EHOSTDOWN, errno.EHOSTUNREACH,
    errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT, errno.EPIPE,
})
"""OS errors a later attempt can outlive: exhausted resources and network reachability."""


class IntegrationError(Exception):
    """An integration failure carrying an operator-safe ``public_message``.

    Subclasses compose the message from facts they own (the login name, the
    host, the server's refusal) and never from raw vendor payloads.
    """

    transient = False

    def __init__(
        self, public_message: str = "", *, transient: bool | None = None,
        retry_after: timedelta | None = None,
    ) -> None:
        """Attach an optional positive provider horizon to this refusal.

        A hint implies transience unless the caller explicitly declares otherwise.
        Subclasses may declare ``transient = True`` and use the same validation.
        Aggregate refusals can retain a partition's horizon while declaring the
        whole operation permanent; a periodic caller still honours that horizon.
        """

        retryable = self.transient if transient is None else transient
        if retry_after is not None:
            if retry_after <= timedelta(0):
                raise ValueError("retry_after must be a positive timedelta.")
            if transient is None:
                retryable = True
        self.transient = retryable
        self.retry_after = retry_after
        super().__init__(public_message)

    @property
    def public_message(self) -> str:
        """Return the bounded text safe to persist and show to operators."""

        return str(self) or INTEGRATION_FAILURE_MESSAGE


class RequestNotSentError(IntegrationError):
    """Connect, DNS or TLS handshake failed before any HTTP request bytes were written.

    Delivery owners may retry this refusal as not sent. Write/read failures after
    sending are deliberately outside this type because their outcome is uncertain.
    """

    transient = True

    def __init__(self) -> None:
        super().__init__("The HTTP request was not sent; the connection could not be established.")


@dataclass(frozen=True, slots=True)
class IntegrationFailure:
    """An integration-owned, safe failure message for persisted telemetry."""

    message: str


def is_transient_failure(error: BaseException) -> bool:
    """Whether retrying an idempotent connection attempt can succeed unchanged.

    An ``IntegrationError`` declares its own transience; ``OAuthFlowError`` does
    so from its HTTP status. Of the unclassified infrastructure failures, only
    the ones a later attempt can outlive are retryable: a refused, reset or
    timed-out connection, a DNS lookup failure, an exhausted OS resource
    (:data:`RETRYABLE_ERRNOS`), an HTTP transport failure or 5xx answer, and a
    dropped database connection. A known-broken setup — a failed certificate
    verification, a missing file or binary, a permission refusal — is not.
    Callers whose attempt may already have caused a side effect (a sent message)
    decide uncertainty themselves instead.
    """

    if isinstance(error, IntegrationError):
        return error.transient
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code >= 500
    if isinstance(error, ssl.SSLCertVerificationError):
        return False
    if isinstance(error, (ConnectionError, TimeoutError, socket.gaierror)):
        return True
    if isinstance(error, OSError):
        return error.errno in RETRYABLE_ERRNOS
    return isinstance(error, (httpx2.TransportError, OperationalError))


def doubling_backoff(attempts: int, *, cap: timedelta) -> timedelta:
    """Return the wait before retry ``attempts``: one minute, doubling, at most ``cap``."""

    return min(timedelta(minutes=2 ** min(max(0, attempts - 1), BACKOFF_EXPONENT_CAP)), cap)


def transient_retry_delay(error: BaseException, attempts: int, *, cap: timedelta) -> timedelta:
    """Return :func:`doubling_backoff`, lengthened to the error's own ``retry_after``."""

    delay = doubling_backoff(attempts, cap=cap)
    hint = error.retry_after if isinstance(error, IntegrationError) else None
    return delay if hint is None else max(delay, hint)


def safe_integration_failure(error: Exception) -> IntegrationFailure:
    """Project safe integration refusals; keep unclassified vendor text private."""

    if isinstance(error, IntegrationError):
        return IntegrationFailure(error.public_message)
    if isinstance(error, ValidationError):
        return IntegrationFailure("Integration configuration is invalid.")
    if isinstance(error, DataError):
        return IntegrationFailure("Database rejected invalid or oversized record data.")
    return IntegrationFailure(INTEGRATION_FAILURE_MESSAGE)
