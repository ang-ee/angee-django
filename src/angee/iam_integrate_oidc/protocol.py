"""OIDC login protocol — OAuth plus the login's nonce-bound authorization request.

The clean extension of the OAuth base: inherits the OAuth2 authorization-code/
refresh behavior and ID-token verification from
:class:`~angee.integrate.oauth.client.OAuthClientProtocol` and adds what a login
requires: the ``openid`` scope and a ``nonce`` on every authorization request, and
discovered endpoints before the code exchange. Bound directly to the single
``OAuthClient`` row; when configured, discovery is owned by that row's
``discover_endpoints()`` method.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.state import StateFlow


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
        flow: StateFlow = StateFlow.LOGIN,
        consent: bool = False,
        **extras: str,
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
            flow=flow,
            consent=consent,
            nonce=nonce,
            **extras,
        )

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
        state: str | None = None,
        scopes: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Exchange an authorization code for tokens, discovering endpoints first."""

        self.ensure_endpoints()
        return super().exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            state=state,
            scopes=scopes,
        )
