"""OIDC protocol — OAuth plus verified identity.

The clean extension of the OAuth base: inherits the OAuth2 authorization-code/
refresh behavior from :class:`~angee.integrate.oauth.client.OAuthClientProtocol`
and adds the OpenID Connect layer: ID-token verification, userinfo enrichment, and
OIDC authorize parameters. Bound directly to the single ``OAuthClient`` row; when
configured, discovery is owned by that row's ``discover_endpoints()`` method.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from typing import Any

import jwt
from django.conf import settings
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError

from angee.integrate.oauth.client import OAuthClientProtocol, oauth_get_json
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
        return self._grant_uri(
            state=state,
            redirect_uri=redirect_uri,
            scopes=effective_scopes,
            code_challenge=code_challenge,
            nonce=nonce,
        )

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

    def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> dict[str, Any]:
        """Verify and return claims from one OIDC ID token."""

        if not id_token:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        if not self.oauth_client.issuer or not self.oauth_client.jwks_uri:
            self.ensure_endpoints()
        issuer, jwks_uri = self.oauth_client.issuer, self.oauth_client.jwks_uri
        if not issuer or not jwks_uri:
            raise OAuthFlowError(MISSING_ENDPOINT, 400)
        try:
            signing_key = _jwks_client(jwks_uri).get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=_ALLOWED_JWT_ALGORITHMS,
                audience=self.oauth_client.client_id,
                issuer=issuer,
                options={"require": ["exp", "iat"], "verify_exp": True, "verify_iss": True, "verify_aud": True},
            )
        except (PyJWKClientError, PyJWTError, ValueError, TypeError) as exc:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400) from exc
        if not isinstance(claims, dict):
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        if nonce is not None and claims.get("nonce") != nonce:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        return claims


class _PinnedPyJWKClient(PyJWKClient):
    """PyJWT JWKS parser whose fetches compose the shared OAuth GET policy."""

    def __init__(self, uri: str) -> None:
        """Bind one JWKS URI with PyJWT's JWK-set TTL cache enabled."""

        super().__init__(
            uri,
            cache_jwk_set=True,
            lifespan=_jwks_ttl_seconds(),
        )

    def fetch_data(self) -> Any:
        """Fetch the JWKS document through ``oauth_get_json``.

        Only the transport is replaced: PyJWT's ``get_jwk_set`` validates,
        parses and caches the payload this returns.
        """

        try:
            return oauth_get_json(self.uri)
        except Exception as exc:
            raise PyJWKClientConnectionError("Failed to fetch the JWKS document.") from exc


@cache
def _jwks_client(jwks_uri: str) -> _PinnedPyJWKClient:
    """Return the process-local JWKS client for one URI."""

    return _PinnedPyJWKClient(jwks_uri)


def _jwks_ttl_seconds() -> int:
    """Return the configured lifetime for PyJWT's cached JWK set."""

    return int(settings.ANGEE_OIDC_JWKS_TTL)
