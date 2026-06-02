"""OIDC flow errors surfaced to callers as stable codes."""


INVALID_STATE = "invalid_state"
DISCOVERY_FAILED = "discovery_failed"
MISSING_ENDPOINT = "missing_endpoint"
TOKEN_EXCHANGE_FAILED = "token_exchange_failed"
INVALID_ID_TOKEN = "invalid_id_token"
USERINFO_FAILED = "userinfo_failed"
IDENTITY_RESOLUTION_FAILED = "identity_resolution_failed"


class OidcFlowError(Exception):
    """Exception carrying a stable OIDC failure code and HTTP status."""

    def __init__(self, code: str, http_status: int = 400, message: str | None = None) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(message or code)
