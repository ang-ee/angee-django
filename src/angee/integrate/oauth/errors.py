"""OAuth/OIDC flow errors surfaced to callers as stable codes.

The shared failure vocabulary for the connection protocol. OAuth owns the base
codes (state, token exchange); the OIDC layer reuses the same
:class:`OAuthFlowError` for its id-token/userinfo failures, and the login addon
adds its own identity-resolution codes on top.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from angee.integrate.errors import IntegrationError

if TYPE_CHECKING:
    from angee.integrate.oauth.state import StateRecord

INVALID_STATE = "invalid_state"
CLIENT_NOT_CONFIGURED = "client_not_configured"
DISCOVERY_FAILED = "discovery_failed"
MISSING_ENDPOINT = "missing_endpoint"
TOKEN_EXCHANGE_FAILED = "token_exchange_failed"
PROVIDER_UNAVAILABLE = "provider_unavailable"
INVALID_ID_TOKEN = "invalid_id_token"
EXTERNAL_ACCOUNT_RESOLUTION_FAILED = "external_account_resolution_failed"
SCOPE_NOT_IN_CATALOGUE = "scope_not_in_catalogue"
ACCOUNT_SWITCH_REFUSED = "account_switch_refused"
CONSENT_REQUIRED = "consent_required"
SCOPE_NOT_GRANTED = "scope_not_granted"
REDIRECT_URI_REQUIRED = "redirect_uri_required"
OAUTH_CLIENT_NOT_CONNECTABLE = "oauth_client_not_connectable"
ACCOUNT_ALREADY_LINKED = "account_already_linked"

_PUBLIC_MESSAGES = {
    INVALID_STATE: "The authorization request is invalid or has expired.",
    CLIENT_NOT_CONFIGURED: "This connection is not configured.",
    DISCOVERY_FAILED: "The provider configuration could not be loaded.",
    MISSING_ENDPOINT: "The provider does not expose the required endpoint.",
    TOKEN_EXCHANGE_FAILED: "The provider could not complete authorization.",
    PROVIDER_UNAVAILABLE: "The provider is temporarily unavailable. Try again later.",
    INVALID_ID_TOKEN: "The provider returned an invalid identity token.",
    EXTERNAL_ACCOUNT_RESOLUTION_FAILED: "The connected account could not be resolved.",
    SCOPE_NOT_IN_CATALOGUE: "This connection does not offer the access this integration needs.",
    ACCOUNT_SWITCH_REFUSED: "This integration is connected to another account; add a new integration for this one.",
    CONSENT_REQUIRED: "The provider must be asked for consent again to keep this connection working.",
    SCOPE_NOT_GRANTED: "The provider did not grant all the access this connection needs; connect again and allow it.",
    REDIRECT_URI_REQUIRED: "An OAuth redirect URI is required.",
    OAUTH_CLIENT_NOT_CONNECTABLE: "This connection is not available.",
    ACCOUNT_ALREADY_LINKED: "This account is already linked.",
}


class OAuthFlowError(IntegrationError):
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
        super().__init__(message or code, transient=http_status >= 500 or http_status == 429)

    @property
    def public_message(self) -> str:
        """Return framework-owned text safe for callers to show to users."""

        return _PUBLIC_MESSAGES.get(self.code, "The authorization request failed.")


class ConsentRequired(OAuthFlowError):
    """A connect grant lacked the refresh token its client needs; restart the flow asking for consent.

    ``record`` is the consumed state of the refused flow, which
    ``flow.restart_with_consent`` starts again.
    """

    def __init__(self, record: StateRecord) -> None:
        super().__init__(CONSENT_REQUIRED, 409)
        self.record = record
