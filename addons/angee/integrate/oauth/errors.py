"""OAuth/OIDC flow errors surfaced to callers as stable codes.

The shared failure vocabulary for the connection protocol. OAuth owns the base
codes (state, token exchange); the OIDC layer reuses the same
:class:`OAuthFlowError` for its id-token/userinfo failures, and the login addon
adds its own identity-resolution codes on top.
"""

from __future__ import annotations

from typing import Any

INVALID_STATE = "invalid_state"
CLIENT_NOT_CONFIGURED = "client_not_configured"
DISCOVERY_FAILED = "discovery_failed"
MISSING_ENDPOINT = "missing_endpoint"
TOKEN_EXCHANGE_FAILED = "token_exchange_failed"
INVALID_ID_TOKEN = "invalid_id_token"
USERINFO_FAILED = "userinfo_failed"
EXTERNAL_ACCOUNT_RESOLUTION_FAILED = "external_account_resolution_failed"

_PUBLIC_MESSAGES = {
    INVALID_STATE: "The authorization request is invalid or has expired.",
    CLIENT_NOT_CONFIGURED: "This connection is not configured.",
    DISCOVERY_FAILED: "The provider configuration could not be loaded.",
    MISSING_ENDPOINT: "The provider does not expose the required endpoint.",
    TOKEN_EXCHANGE_FAILED: "The provider could not complete authorization.",
    INVALID_ID_TOKEN: "The provider returned an invalid identity token.",
    USERINFO_FAILED: "The provider identity could not be loaded.",
    EXTERNAL_ACCOUNT_RESOLUTION_FAILED: "The connected account could not be resolved.",
    "redirect_uri_required": "An OAuth redirect URI is required.",
    "oauth_client_not_connectable": "This connection is not available.",
    "account_already_linked": "This account is already linked.",
}


class OAuthFlowError(Exception):
    """Exception carrying a stable OAuth/OIDC failure code and HTTP status."""

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
    def public_message(self) -> str:
        """Return framework-owned text safe for callers to show to users."""

        return _PUBLIC_MESSAGES.get(self.code, "The authorization request failed.")
