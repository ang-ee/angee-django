"""OIDC protocol — OAuth plus verified identity.

The clean extension of the OAuth base: inherits the OAuth2 authorization-code/
refresh behavior from :class:`~angee.integrate.oauth.client.OAuthClientProtocol`
and adds the OpenID Connect layer: ID-token verification, userinfo enrichment, and
OIDC authorize parameters. Bound directly to the single ``OAuthClient`` row; when
configured, discovery is owned by that row's ``discover_endpoints()`` method.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError

from angee.integrate.oauth.client import (
    _USER_AGENT,
    HTTP_TIMEOUT_SECONDS,
    OAuthClientProtocol,
    _with_query,
)
from angee.integrate.oauth.errors import (
    INVALID_ID_TOKEN,
    MISSING_ENDPOINT,
    OAuthFlowError,
)

_ALLOWED_JWT_ALGORITHMS = (
    "RS256",
    "ES256",
)


class OAuthClientOidcProtocol(OAuthClientProtocol):
    """OIDC login protocol for one OAuth client with OIDC login fields."""

    def __init__(self, oauth_client: Any) -> None:
        """Bind to one OAuth client row."""

        super().__init__(oauth_client)

    def authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: Iterable[str],
        nonce: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        """Return the OIDC authorization URL — adds the ``openid`` scope and a ``nonce``.

        ``nonce`` is optional only to keep this substitutable for the OAuth base
        ``authorize_url``; an OIDC login always binds one (it is verified back in the
        ID token), so a missing nonce is a programming error.
        """

        if nonce is None:
            raise ValueError("OIDC authorize requires a nonce.")
        self.ensure_endpoints()
        effective_scopes = list(scopes)
        if "openid" not in effective_scopes:
            effective_scopes.insert(0, "openid")
        query = self._authorize_query(
            state=state,
            redirect_uri=redirect_uri,
            scopes=effective_scopes,
            code_challenge=code_challenge,
        )
        query["nonce"] = nonce
        return _with_query(self._endpoint("authorize_endpoint"), query)

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Exchange an authorization code for tokens, discovering endpoints first."""

        self.ensure_endpoints()
        return super().exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            state=state,
        )

    def verify_id_token(
        self,
        id_token: str,
        *,
        nonce: str | None = None,
        _jwks_client: Any | None = None,
    ) -> dict[str, Any]:
        """Verify and return claims from one OIDC ID token."""

        if not id_token:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        issuer = str(getattr(self.oauth_client, "issuer", "") or "")
        jwks_uri = str(getattr(self.oauth_client, "jwks_uri", "") or "")
        if not issuer or not jwks_uri:
            self.ensure_endpoints()
            issuer = str(getattr(self.oauth_client, "issuer", "") or "")
            jwks_uri = str(getattr(self.oauth_client, "jwks_uri", "") or "")
        if not issuer or not jwks_uri:
            raise OAuthFlowError(MISSING_ENDPOINT, 400)
        client_id = str(getattr(self.oauth_client, "client_id", ""))
        try:
            jwks_client = _jwks_client or PyJWKClient(
                jwks_uri,
                headers={"User-Agent": _USER_AGENT},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            signing_key = jwks_client.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=_ALLOWED_JWT_ALGORITHMS,
                audience=client_id,
                issuer=issuer,
                options={"require": ["exp", "iat"], "verify_exp": True},
            )
        except (PyJWKClientError, PyJWTError, ValueError, TypeError) as exc:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400) from exc
        if not isinstance(claims, dict):
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        if claims.get("iss") != issuer:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        if not _audience_matches(claims.get("aud"), client_id):
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        if nonce is not None and claims.get("nonce") != nonce:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        return claims

    def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """Fetch userinfo, resolving the endpoint from discovery when it is blank."""

        if access_token and not str(getattr(self.oauth_client, "userinfo_endpoint", "") or ""):
            try:
                self.ensure_endpoints()
            except OAuthFlowError:
                return {}
        return super().fetch_userinfo(access_token)

    def ensure_endpoints(self) -> dict[str, Any]:
        """Fill blank endpoints on the OAuth client via discovery.

        A no-op when the client carries no ``discovery_url`` (endpoints are then
        configured explicitly). The discovery document is cached per URL, so an
        explicitly-configured provider that also sets a discovery URL fetches at
        most once. In-memory only: the fetched endpoints serve the current request
        and are not persisted unless the caller saves the client.
        """

        return dict(self.oauth_client.discover_endpoints())


def _audience_matches(value: object, expected: str) -> bool:
    """Return whether an OIDC ``aud`` claim contains ``expected``."""

    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in value
    return False
