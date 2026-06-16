"""OIDC protocol — OAuth plus verified identity.

The clean extension of the OAuth base: inherits the OAuth2 authorization-code/
refresh behavior from :class:`~angee.integrate.oauth.client.OAuthClientProtocol`
and adds the OpenID Connect layer — ``.well-known`` discovery (filling blank
endpoints across the OAuth client and its OIDC refinement), ID-token
verification, and the userinfo fetch. Bound to an ``OidcClient`` (a refinement of
an ``integrate.OAuthClient``). The authorize request requires the ``openid`` scope
and a ``nonce``; every operation ensures endpoints from discovery first.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

import jwt
from django.conf import settings
from django.core.cache import cache
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError

from angee.integrate.oauth.client import (
    _USER_AGENT,
    HTTP_TIMEOUT_SECONDS,
    OAuthClientProtocol,
    _get_json,
    _with_query,
)
from angee.integrate.oauth.errors import (
    DISCOVERY_FAILED,
    INVALID_ID_TOKEN,
    MISSING_ENDPOINT,
    OAuthFlowError,
)

_DEFAULT_DISCOVERY_TTL_SECONDS = 3600
_DISCOVERY_CACHE_PREFIX = "angee.iam_integrate_oidc.discovery:"
_ALLOWED_JWT_ALGORITHMS = (
    "RS256",
    "ES256",
)
# Discovery fills blank endpoints across both rows: the OAuth base owns the
# transport and userinfo endpoints; the OIDC refinement owns issuer/JWKS.
_OAUTH_DISCOVERY_FIELDS = {
    "authorize_endpoint": "authorization_endpoint",
    "token_endpoint": "token_endpoint",
    "revoke_endpoint": "revocation_endpoint",
    "userinfo_endpoint": "userinfo_endpoint",
}
_OIDC_DISCOVERY_FIELDS = {
    "issuer": "issuer",
    "jwks_uri": "jwks_uri",
}


class OidcClientProtocol(OAuthClientProtocol):
    """OIDC login protocol for one ``OidcClient`` (an ``OAuthClient`` refinement)."""

    def __init__(self, oidc_client: Any) -> None:
        """Bind to an ``OidcClient`` row, reusing its ``OAuthClient`` for transport."""

        super().__init__(oidc_client.oauth_client)
        self.oidc = oidc_client

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
        issuer = str(getattr(self.oidc, "issuer", "") or "")
        jwks_uri = str(getattr(self.oidc, "jwks_uri", "") or "")
        if not issuer or not jwks_uri:
            self.ensure_endpoints()
            issuer = str(getattr(self.oidc, "issuer", "") or "")
            jwks_uri = str(getattr(self.oidc, "jwks_uri", "") or "")
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
            except Exception:
                return {}
        return super().fetch_userinfo(access_token)

    def ensure_endpoints(self) -> dict[str, Any]:
        """Fill blank endpoints on the OAuth client and its OIDC refinement via discovery.

        A no-op when the refinement carries no ``discovery_url`` (endpoints are then
        configured explicitly). The discovery document is cached per URL, so an
        explicitly-configured provider that also sets a discovery URL fetches at most
        once. In-memory only: the fetched endpoints serve the current request and are
        not persisted.
        """

        discovery_url = str(getattr(self.oidc, "discovery_url", "") or "")
        if not discovery_url:
            return {}
        discovery = self._discovery_document(discovery_url)
        for field, key in _OAUTH_DISCOVERY_FIELDS.items():
            if not getattr(self.oauth_client, field, ""):
                value = discovery.get(key)
                if value:
                    setattr(self.oauth_client, field, str(value))
        for field, key in _OIDC_DISCOVERY_FIELDS.items():
            if not getattr(self.oidc, field, ""):
                value = discovery.get(key)
                if value:
                    setattr(self.oidc, field, str(value))
        return discovery

    def _discovery_document(self, discovery_url: str) -> dict[str, Any]:
        """Return the cached or freshly fetched OIDC discovery document."""

        cache_key = _discovery_cache_key(discovery_url)
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        try:
            discovery = _get_json(discovery_url, error_code=DISCOVERY_FAILED)
        except OAuthFlowError:
            raise
        except Exception as exc:
            raise OAuthFlowError(DISCOVERY_FAILED, 400) from exc
        cache.set(cache_key, discovery, timeout=_discovery_ttl_seconds())
        return discovery


def _discovery_cache_key(discovery_url: str) -> str:
    """Return the cache key for one OIDC discovery URL."""

    digest = hashlib.sha256(discovery_url.encode("utf-8")).hexdigest()
    return f"{_DISCOVERY_CACHE_PREFIX}{digest}"


def _discovery_ttl_seconds() -> int:
    """Return the configured lifetime for cached OIDC discovery documents."""

    return int(getattr(settings, "ANGEE_OIDC_DISCOVERY_TTL", _DEFAULT_DISCOVERY_TTL_SECONDS))


def _audience_matches(value: object, expected: str) -> bool:
    """Return whether an OIDC ``aud`` claim contains ``expected``."""

    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in value
    return False
