"""Outbound webhook delivery: a signed POST over the shared HTTP client.

The delivery *orchestration* (which subscriptions receive an event) lives on
``WebhookSubscription``'s manager, and a subscription delivers itself via
``WebhookSubscription.deliver``. This module owns only the webhook *signature*
and maps the shared client's result to a delivery error — the SSRF-pinned
transport is :class:`angee.integrate.http.HttpClient`, so the resolve-once-and-pin
mechanics live in exactly one place.
"""

from __future__ import annotations

import hashlib
import hmac

from angee.integrate.http import HttpClient
from angee.integrate.net import parse_http_url

SIGNATURE_HEADER = "X-Angee-Signature"
"""Header carrying the HMAC-SHA256 signature for outbound webhook bodies."""


class WebhookDeliveryError(Exception):
    """Raised when a webhook endpoint returns a non-success HTTP status."""

    def __init__(self, message: str, *, status: str = "") -> None:
        """Record the delivery status when one is available."""

        super().__init__(message)
        self.status = status


class PinnedWebhookClient:
    """Posts a signed body to one HTTP(S) URL over the shared SSRF-pinned client.

    The shared client resolves the host once, rejects any non-public answer, and
    dials the pinned address, so a DNS rebind between check and connect cannot
    redirect the POST; this class adds only the Angee webhook signature.
    """

    def __init__(self, url: str) -> None:
        """Parse and gate the delivery URL (scheme + host) up front."""

        parse_http_url(url)
        self._url = url

    def post(self, *, secret: str, body: bytes) -> str:
        """POST ``body`` signed with ``secret`` and return the HTTP status, or raise."""

        response = HttpClient().post(
            self._url,
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                SIGNATURE_HEADER: self._signature(secret, body),
            },
        )
        if not response.ok:
            raise WebhookDeliveryError(f"HTTP {response.status}", status=str(response.status))
        return str(response.status)

    @staticmethod
    def _signature(secret: str, body: bytes) -> str:
        """Return the Angee webhook signature header value for ``body``."""

        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"
