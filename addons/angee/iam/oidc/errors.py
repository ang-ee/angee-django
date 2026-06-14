"""OIDC flow errors surfaced to callers as stable codes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INVALID_STATE = "invalid_state"
CLIENT_NOT_CONFIGURED = "client_not_configured"
DISCOVERY_FAILED = "discovery_failed"
MISSING_ENDPOINT = "missing_endpoint"
TOKEN_EXCHANGE_FAILED = "token_exchange_failed"
INVALID_ID_TOKEN = "invalid_id_token"
USERINFO_FAILED = "userinfo_failed"
IDENTITY_RESOLUTION_FAILED = "identity_resolution_failed"
EXTERNAL_ACCOUNT_RESOLUTION_FAILED = "external_account_resolution_failed"


class OidcFlowError(Exception):
    """Exception carrying a stable OIDC failure code and HTTP status."""

    def __init__(
        self,
        code: str,
        http_status: int = 400,
        message: str | None = None,
        *,
        body: Any = None,
    ) -> None:
        self.code = code
        self.http_status = http_status
        self.body = body
        super().__init__(message or code)

    @property
    def provider_message(self) -> str:
        """Return a safe human message decoded from the provider error ``body``, or ``""``.

        The error owns the shape of its own ``body``: only known scalar fields
        (``error_description``/``message``/``detail``, or a nested ``error``
        object) are surfaced, never an arbitrary response body.
        """

        return _provider_message(self.body)


def _provider_message(body: Any) -> str:
    """Extract a provider error message from one response body without leaking it."""

    if not isinstance(body, Mapping):
        return ""
    for key in ("error_description", "message", "detail"):
        value = _scalar_message(body.get(key))
        if value:
            return value
    nested = body.get("error")
    if isinstance(nested, Mapping):
        value = _provider_message(nested)
        if value:
            return value
    return _scalar_message(nested)


def _scalar_message(value: Any) -> str:
    """Return trimmed provider error text, or ``""`` for non-string values."""

    if not isinstance(value, str):
        return ""
    return value.strip()
