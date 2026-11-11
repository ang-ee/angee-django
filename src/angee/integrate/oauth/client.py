"""Stateless OAuth2 protocol bound to one ``OAuthClient`` registration row.

The base of the connection protocol: authorization-code redirect, code exchange,
refresh, and revocation — everything needed to connect an external account for
API access (Gemini, Grok, Anthropic), with no identity/login concern. OIDC login
extends this in ``angee.iam_integrate_oidc.protocol.OAuthClientOidcProtocol``.

The OAuth2 protocol mechanism (token request, client authentication, PKCE, and
the token-response parsing) is owned by Authlib's ``OAuth2Client`` over httpx2;
this module is the thin per-row adapter behind a stable seam. Authlib's
``prepare_grant_uri`` also builds the authorization URL from the row's endpoint
and client id, with no client session or secret (the OIDC layer adds its
nonce), and the small
``token_request_format == "json"`` provider quirk — a non-standard JSON
token body Authlib does not emit, plus Anthropic's required ``state`` echo —
keeps a documented shim.

Endpoints are taken from the row as configured; when a row has a discovery URL,
the protocol asks the row to fill missing OAuth endpoints before failing. OIDC
login extends this in ``iam_integrate_oidc`` for ID-token/userinfo verification.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal
from urllib import parse

import httpx2
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri

from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import HTTP_TIMEOUT_SECONDS, HttpClient
from angee.integrate.oauth.errors import (
    CLIENT_NOT_CONFIGURED,
    MISSING_ENDPOINT,
    TOKEN_EXCHANGE_FAILED,
    OAuthFlowError,
)

if TYPE_CHECKING:
    from angee.integrate.models import OAuthClient

# An honest, non-browser User-Agent for all outbound connection requests. Must NOT
# spoof a browser: Anthropic's edge denylists browser/curl User-Agents with a 429
# ``rate_limit_error`` (and blocks the HTTP client's default UA with a 403), while
# an honest client UA passes. httpx2 defaults to ``python-httpx2/…``, so the session
# and the GET helper set this explicitly. See docs/backend/guidelines.md (Pitfalls).
USER_AGENT = "Angee-Integrate/1.0"
# Headers every outbound OAuth request carries. Angee parses only JSON bodies —
# Authlib's token parser calls ``response.json()`` unconditionally — so the request
# must say so: RFC 6749 §5.1 specifies a JSON token response, but GitHub's token
# endpoint answers ``application/x-www-form-urlencoded`` unless ``Accept`` asks
# otherwise, which a JSON-only parser then fails to read.
OUTBOUND_HEADERS = {"Accept": "application/json", "User-Agent": USER_AGENT}
logger = logging.getLogger(__name__)

# The token-response keys Angee carries forward; everything else the provider
# returns is dropped. ``credentials.py`` reads access_token/expires_in/scope and
# the refresh token, and the OIDC layer reads id_token.
_TOKEN_MATERIAL_KEYS = ("access_token", "refresh_token", "id_token", "expires_in", "scope")
# Some OAuth providers return stable account metadata with the token response.
# It is not credential material, but it can identify an account when a restricted
# access token cannot call the provider's userinfo endpoint (Anthropic's long-lived
# inference-only token is one such case).
_TOKEN_RESPONSE_IDENTITY_KEYS = ("account", "organization")
_GRANT_URI_ARGUMENTS = frozenset({"uri", "client_id", "response_type", "redirect_uri", "scope", "state"})
"""``prepare_grant_uri``'s own arguments, which a provider's authorize extras never replace."""


def oauth_get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    """GET OAuth JSON with honest headers and support for configured private identity providers.

    The shared HTTP client owns transport and decoding. Discovery and userinfo
    retain their distinct failure policies at their callers.
    """

    return HttpClient().get_json(url, headers={**OUTBOUND_HEADERS, **(headers or {})}, allow_private=True)


class OAuthClientProtocol:
    """OAuth2 authorization-code + refresh protocol for one ``OAuthClient`` row."""

    def __init__(self, oauth_client: OAuthClient) -> None:
        """Bind the protocol to one OAuth client registration row."""

        self.oauth_client = oauth_client
        self._token_response_claims: dict[str, Any] = {}

    @property
    def token_response_claims(self) -> dict[str, Any]:
        """Return non-secret identity metadata carried by the last token response."""

        return dict(self._token_response_claims)

    def authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: Iterable[str],
        code_challenge: str | None = None,
    ) -> str:
        """Return the provider authorization URL for one OAuth code flow."""

        return self._grant_uri(state=state, redirect_uri=redirect_uri, scopes=scopes, code_challenge=code_challenge)

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Exchange an authorization code for token material.

        ``state`` is part of the redirect seam. The standard form path validates
        it before exchange and leaves it out of the token request (RFC 6749
        §4.1.3); the JSON shim carries it because Anthropic's public-client token
        endpoint rejects that non-standard JSON request as malformed without it.
        """

        grant: dict[str, Any] = {"code": code, "redirect_uri": redirect_uri}
        if self.oauth_client.supports_pkce and code_verifier:
            grant["code_verifier"] = code_verifier
        if self._uses_json_token_request() and state:
            grant["state"] = state
        return self.exchange_grant("authorization_code", **grant)

    def refresh_token(self, *, refresh_token: str) -> dict[str, Any]:
        """Exchange a stored refresh token for fresh token material (RFC 6749 §6).

        The provider may rotate the refresh token; when it returns a new one the
        caller persists it. Raises ``OAuthFlowError`` when the grant is rejected.
        """

        return self.exchange_grant("refresh_token", refresh_token=refresh_token)

    def exchange_identity(
        self, *, code: str, redirect_uri: str, code_verifier: str | None = None, state: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Return refined material, userinfo and the code exchange's OIDC proof.

        Refinement may replace or omit an ID token; only the authorization-code
        exchange's token proves the login against the flow's nonce.
        """

        tokens = self.exchange_code(
            code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, state=state,
        )
        id_token = str(tokens.get("id_token", "") or "")
        tokens = self.oauth_client.refine_grant(self, tokens)
        access_token = str(tokens.get("access_token", "") or "")
        claims = self.fetch_userinfo(
            access_token, params=self.oauth_client.userinfo_params(self, access_token),
        )
        return tokens, claims, id_token

    def fetch_userinfo(self, access_token: str, *, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Best-effort fetch of profile claims with one OAuth access token.

        Reads the access-token-protected ``userinfo_endpoint`` to label a connected
        account (connect needs no ID token). Blank endpoints use client-owned
        discovery for OAuth and OIDC alike.
        """

        if not access_token:
            return {}
        try:
            userinfo_endpoint = str(self.oauth_client.userinfo_endpoint or "")
            if not userinfo_endpoint and self.oauth_client.discovery_url:
                self.ensure_endpoints()
                userinfo_endpoint = str(self.oauth_client.userinfo_endpoint or "")
            if not userinfo_endpoint:
                return {}
            claims = oauth_get_json(
                with_query(userinfo_endpoint, params or {}),
                headers={"Authorization": f"Bearer {access_token}"},
            )
            return claims if isinstance(claims, dict) else {}
        except Exception:
            return {}

    def revoke_token(self, token: str) -> None:
        """Best-effort RFC 7009 token revocation for an OAuth credential."""

        revoke_endpoint = self.oauth_client.revoke_endpoint
        if not revoke_endpoint or not token:
            return
        session = self._session()
        try:
            session.revoke_token(revoke_endpoint, token=token, token_type_hint="access_token")
        except RequestNotSentError as exc:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 503) from exc
        except OAuthError, httpx2.HTTPError:
            # Revocation is best-effort: a provider that rejects or omits the endpoint
            # does not block disconnect.
            pass
        finally:
            session.close()

    def ensure_endpoints(self) -> dict[str, Any]:
        """Ask the OAuth client row to fill blank endpoints from OIDC metadata discovery.

        A no-op when the row carries no ``discovery_url`` (its endpoints are then
        configured explicitly). The document is cached per URL, and the filled
        endpoints serve this protocol's request in memory; the operator's
        discovery action persists them (``discover_endpoints(persist=True)``).
        """

        return dict(self.oauth_client.discover_endpoints())

    def exchange_grant(self, grant_type: str, **grant: Any) -> dict[str, Any]:
        """Exchange a standard or provider-specific grant for validated material.

        Standard providers go through Authlib's ``OAuth2Client`` (form-encoded per
        RFC 6749). A provider that declares ``token_request_format == "json"`` —
        a non-standard body Authlib does not emit — takes the JSON shim. Both paths
        merge the row's ``token_param_values`` and return the same token shape.
        """

        token_endpoint = self._endpoint("token_endpoint")
        self._token_response_claims = {}
        extra = self.oauth_client.provider.token_params(grant_type, self.oauth_client.token_param_values)
        if self._uses_json_token_request():
            return self._json_token_request(token_endpoint, grant_type, grant, extra)
        return self._authlib_token_request(token_endpoint, grant_type, grant, extra)

    def _uses_json_token_request(self) -> bool:
        """Return whether this row uses the non-standard JSON token shim."""

        return self.oauth_client.token_request_format_value == "json"

    def _authlib_token_request(
        self,
        endpoint: str,
        grant_type: str,
        grant: Mapping[str, Any],
        extra: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Run the standard (form-encoded) grant through Authlib's OAuth2 client."""

        session = self._session()
        try:
            if grant_type == "refresh_token":
                token = session.refresh_token(endpoint, **grant, **extra)
            else:
                token = session.fetch_token(endpoint, grant_type=grant_type, **grant, **extra)
        except OAuthError as exc:
            body = {"error": exc.error, "error_description": exc.description}
            self._log_token_failure("oauth_refusal", None)
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400, body=body) from exc
        except httpx2.HTTPStatusError as exc:
            # Authlib raises this only for >=500 (it raise_for_status()es server errors).
            self._log_token_failure(exc.response.status_code, _response_body(exc.response))
            raise OAuthFlowError(
                TOKEN_EXCHANGE_FAILED,
                exc.response.status_code,
                body=_response_body(exc.response),
            ) from exc
        except RequestNotSentError as exc:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 503) from exc
        except (httpx2.HTTPError, ValueError) as exc:
            # Authlib parses the token body as JSON before checking the status, so a
            # non-JSON 4xx (the documented Anthropic/CDN 403/429 block page) surfaces as
            # a ValueError, and a transport failure as httpx2.HTTPError — both map to the
            # stable OAuthFlowError seam, like the JSON-shim and discovery paths.
            self._log_token_failure(f"transport_error:{type(exc).__name__}", None)
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 503 if isinstance(exc, httpx2.TransportError) else 400) from exc
        finally:
            session.close()
        response = dict(token)
        self._token_response_claims = _token_response_identity_claims(response)
        return _token_material(response)

    def _json_token_request(
        self,
        endpoint: str,
        grant_type: str,
        grant: Mapping[str, Any],
        extra: Mapping[str, Any],
    ) -> dict[str, Any]:
        """POST a non-standard JSON token body (provider quirk Authlib does not emit)."""

        body: dict[str, Any] = {
            **extra,
            "client_id": self.oauth_client.client_id,
            "grant_type": grant_type,
            **grant,
        }
        client_secret = self._client_secret()
        if client_secret:
            body["client_secret"] = client_secret
        client = self._http_client()
        try:
            response = client.post(endpoint, json=body)
            response.raise_for_status()
            data = response.json()
        except httpx2.HTTPStatusError as exc:
            self._log_token_failure(exc.response.status_code, _response_body(exc.response))
            raise OAuthFlowError(
                TOKEN_EXCHANGE_FAILED,
                exc.response.status_code,
                body=_response_body(exc.response),
            ) from exc
        except RequestNotSentError as exc:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 503) from exc
        except (httpx2.HTTPError, ValueError) as exc:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 503 if isinstance(exc, httpx2.TransportError) else 400) from exc
        finally:
            client.close()
        if not isinstance(data, dict):
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)
        self._token_response_claims = _token_response_identity_claims(data)
        return _token_material(data)

    def _session(self) -> OAuth2Client:
        """Build the per-row Authlib OAuth2 client.

        Client authentication rides the body (``client_secret_post``) to match the
        row's stored secret; a public client (no secret) sends only ``client_id``.
        Scope is omitted so refresh requests stay minimal — scopes are owned by the
        authorize URL. Authlib auto-refresh is left off so a rotated refresh token
        cannot be replayed behind ``Credential`` refresh locking.
        """

        secret = self._client_secret()
        return OAuth2Client(
            client_id=self.oauth_client.client_id,
            client_secret=secret or None,
            token_endpoint_auth_method="client_secret_post" if secret else "none",
            headers=dict(OUTBOUND_HEADERS),
            **_outbound_kwargs(),
        )

    def _client_secret(self) -> str:
        """Use the stored token-endpoint secret, skipping storage for fixed public clients.

        ``manual_redirect_uri`` declares the RFC 8252-style fixed public client
        whose callback allow-list Angee cannot extend.  Such a client authenticates
        its authorization-code grant with PKCE and has no client secret, so touching
        the encrypted field is both unnecessary and wrong: an unreadable historical
        encrypted blank must not block its public-client exchange. Other rows use
        their stored secret when present and auth ``none`` when blank. Unreadable
        secrets retain the encrypted descriptor's fail-closed behavior.
        """

        if self.oauth_client.is_public_client:
            return ""
        return str(self.oauth_client.client_secret or "")

    def confidential_client_secret(self) -> str:
        """Return confidential material for server-side request signing.

        Public clients are refused before touching encrypted secret storage.
        Confidential clients require a secret and retain the encrypted field's
        fail-closed behavior.
        Never log or expose the returned value.
        """

        if self.oauth_client.is_public_client:
            raise IntegrationError("Public OAuth clients cannot sign requests with a client secret.")
        secret = str(self.oauth_client.client_secret or "")
        if not secret:
            raise IntegrationError("The confidential OAuth client has no client secret.")
        return secret

    def _http_client(self) -> httpx2.Client:
        """Return a plain httpx2 client carrying the outbound headers (JSON-shim transport)."""

        return httpx2.Client(headers=dict(OUTBOUND_HEADERS), **_outbound_kwargs())

    def _log_token_failure(self, status: object, body: Any) -> None:
        """Log bounded token-request metadata without provider-controlled values."""

        del body
        client_label = self.oauth_client.slug or "unknown"
        logger.warning(
            "OAuth token request failed for %s: status=%s",
            client_label,
            status,
        )

    def _grant_uri(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: Iterable[str],
        code_challenge: str | None,
        **extras: str,
    ) -> str:
        """Build the authorization URL with Authlib's ``prepare_grant_uri`` (OIDC adds ``nonce``).

        Provider authorize parameters and the flow's ``extras`` ride as extra
        query parameters and never replace the grant's own arguments; an empty
        scope is omitted. A flow needs PKCE or a client secret: with neither,
        nothing would authenticate the code exchange, so a misconfigured
        confidential client is refused before the provider redirect. With PKCE
        the secret is never read.
        """

        challenge = code_challenge if self.oauth_client.supports_pkce else None
        if not challenge and not self._client_secret():
            raise OAuthFlowError(CLIENT_NOT_CONFIGURED, 400, "OAuth client needs PKCE or a client secret.")
        params = {
            key: value
            for key, value in self.oauth_client.authorize_param_values.items()
            if key not in _GRANT_URI_ARGUMENTS
        }
        if challenge:
            params.update(code_challenge=challenge, code_challenge_method="S256")
        params.update(extras)
        return prepare_grant_uri(
            self._endpoint("authorize_endpoint"),
            str(self.oauth_client.client_id),
            "code",
            redirect_uri,
            list(scopes),
            state,
            **params,
        )

    def _endpoint(self, field: Literal["authorize_endpoint", "token_endpoint"]) -> str:
        """Return one endpoint field, discovering blank endpoints first, or raise when still blank."""

        if not getattr(self.oauth_client, field):
            self.ensure_endpoints()
        value = str(getattr(self.oauth_client, field))
        if not value:
            raise OAuthFlowError(MISSING_ENDPOINT, 400)
        return value


def _outbound_kwargs() -> dict[str, Any]:
    """Shared outbound httpx2 client policy: a request timeout and the transport.

    The transport is ``HttpClient.transport_factory``'s, the SSRF-pinned
    ``PinnedTransport`` unless a test substitutes the network there, so OAuth,
    discovery, and userinfo get the same address pinning and system-store TLS as
    every other outbound call. Authlib's ``OAuth2Client`` is an ``httpx2.Client``,
    so the transport must be an httpx2 transport too.
    ``allow_private=True`` permits operator-configured self-hosted IDPs on private
    networks while still rejecting cloud metadata and the other SSRF escapes.
    """

    return {
        "timeout": HTTP_TIMEOUT_SECONDS,
        "transport": HttpClient.transport_factory(allow_private=True),
    }


def with_query(url: str, query: Mapping[str, str]) -> str:
    """Return ``url`` with ``query`` appended or merged."""

    parts = parse.urlsplit(url)
    existing = parse.parse_qsl(parts.query, keep_blank_values=True)
    encoded = parse.urlencode([*(pair for pair in existing if pair[0] not in query), *query.items()])
    return parse.urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))


def _token_material(response: Mapping[str, Any]) -> dict[str, Any]:
    """Return the carried token keys from a token response, requiring an access token."""

    tokens = {key: response[key] for key in _TOKEN_MATERIAL_KEYS if key in response}
    if not tokens.get("access_token"):
        raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)
    return tokens


def _token_response_identity_claims(response: Mapping[str, Any]) -> dict[str, Any]:
    """Return allow-listed, non-secret identity mappings from a token response."""

    return {
        key: dict(value) for key in _TOKEN_RESPONSE_IDENTITY_KEYS if isinstance((value := response.get(key)), Mapping)
    }


def _response_body(response: httpx2.Response) -> Any:
    """Return a JSON or text response body from an httpx2 response."""

    try:
        return response.json()
    except ValueError:
        return response.text
