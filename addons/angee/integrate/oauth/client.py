"""Stateless OAuth2 protocol bound to one ``OAuthClient`` registration row.

The base of the connection protocol: authorization-code redirect, code exchange,
refresh, and revocation — everything needed to connect an external account for
API access (Gemini, Grok, Anthropic), with no identity/login concern. OIDC login
extends this in ``angee.iam_integrate_oidc.protocol.OAuthClientOidcProtocol``.

The OAuth2 protocol mechanism (token request, client authentication, PKCE, and
the token-response parsing) is owned by Authlib's ``OAuth2Client`` over httpx;
this module is the thin per-row adapter behind a stable seam. The authorization
URL is still built here (a deterministic string the OIDC layer extends), and the
small ``token_request_format == "json"`` provider quirk — a non-standard JSON
token body Authlib does not emit, plus Anthropic's required ``state`` echo —
keeps a documented shim.

Endpoints are taken from the row as configured; when a row has a discovery URL,
the protocol asks the row to fill missing OAuth endpoints before failing. OIDC
login extends this in ``iam_integrate_oidc`` for ID-token/userinfo verification.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any
from urllib import parse

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import OAuth2Client

from angee.integrate.http import PinnedTransport
from angee.integrate.oauth.errors import (
    MISSING_ENDPOINT,
    TOKEN_EXCHANGE_FAILED,
    USERINFO_FAILED,
    OAuthFlowError,
)

HTTP_TIMEOUT_SECONDS = 10
# An honest, non-browser User-Agent for all outbound connection requests. Must NOT
# spoof a browser: Anthropic's edge denylists browser/curl User-Agents with a 429
# ``rate_limit_error`` (and blocks the HTTP client's default UA with a 403), while
# an honest client UA passes. httpx defaults to ``python-httpx/…``, so the session
# and the GET helper set this explicitly. See docs/backend/guidelines.md (Pitfalls).
USER_AGENT = "Angee-Integrate/1.0"
logger = logging.getLogger(__name__)

# The token-response keys Angee carries forward; everything else the provider
# returns is dropped. ``credentials.py`` reads access_token/expires_in/scope and
# the refresh token, and the OIDC layer reads id_token.
_TOKEN_MATERIAL_KEYS = ("access_token", "refresh_token", "id_token", "expires_in", "scope")


class OAuthClientProtocol:
    """OAuth2 authorization-code + refresh protocol for one ``OAuthClient`` row."""

    def __init__(self, oauth_client: Any) -> None:
        """Bind the protocol to one OAuth client registration row."""

        self.oauth_client = oauth_client
        # Test seam: an injected httpx transport (e.g. ``httpx.MockTransport``) used
        # by the per-row session and the JSON shim. ``None`` dials for real.
        self._transport: httpx.BaseTransport | None = None

    def authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: Iterable[str],
        code_challenge: str | None = None,
    ) -> str:
        """Return the provider authorization URL for one OAuth code flow."""

        query = self._authorize_query(
            state=state,
            redirect_uri=redirect_uri,
            scopes=scopes,
            code_challenge=code_challenge,
        )
        return with_query(self._endpoint("authorize_endpoint"), query)

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
        if getattr(self.oauth_client, "supports_pkce", False) and code_verifier:
            grant["code_verifier"] = code_verifier
        if self._uses_json_token_request() and state:
            grant["state"] = state
        return self._token_request("authorization_code", grant)

    def refresh_token(self, *, refresh_token: str) -> dict[str, Any]:
        """Exchange a stored refresh token for fresh token material (RFC 6749 §6).

        The provider may rotate the refresh token; when it returns a new one the
        caller persists it. Raises ``OAuthFlowError`` when the grant is rejected.
        """

        return self._token_request("refresh_token", {"refresh_token": refresh_token})

    def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """Best-effort fetch of profile claims with one OAuth access token.

        Reads the access-token-protected ``userinfo_endpoint`` to label a connected
        account (connect needs no ID token). The OIDC layer overrides this to resolve
        the endpoint from discovery first.
        """

        if not access_token:
            return {}
        userinfo_endpoint = str(getattr(self.oauth_client, "userinfo_endpoint", "") or "")
        if not userinfo_endpoint:
            return {}
        try:
            return _get_json(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                error_code=USERINFO_FAILED,
                _transport=self._transport,
            )
        except Exception:
            return {}

    def revoke_token(self, token: str) -> None:
        """Best-effort RFC 7009 token revocation for an OAuth credential."""

        revoke_endpoint = str(getattr(self.oauth_client, "revoke_endpoint", "") or "")
        if not revoke_endpoint or not token:
            return
        session = self._session()
        try:
            session.revoke_token(revoke_endpoint, token=token, token_type_hint="access_token")
        except OAuthError, httpx.HTTPError:
            # Revocation is best-effort: a provider that rejects or omits the endpoint
            # does not block disconnect.
            pass
        finally:
            session.close()

    def ensure_endpoints(self) -> dict[str, Any]:
        """Ask the OAuth client row to fill endpoint fields from discovery."""

        discover = getattr(self.oauth_client, "discover_endpoints", None)
        if not callable(discover):
            return {}
        return dict(discover())

    def _token_request(self, grant_type: str, grant: Mapping[str, Any]) -> dict[str, Any]:
        """Dispatch one grant to the token endpoint and return validated material.

        Standard providers go through Authlib's ``OAuth2Client`` (form-encoded per
        RFC 6749). A provider that declares ``token_request_format == "json"`` —
        a non-standard body Authlib does not emit — takes the JSON shim. Both paths
        merge the row's ``token_param_values`` and return the same token shape.
        """

        token_endpoint = self._endpoint("token_endpoint")
        extra = _param_values(self.oauth_client, "token_param_values")
        if self._uses_json_token_request():
            return self._json_token_request(token_endpoint, grant_type, grant, extra)
        return self._authlib_token_request(token_endpoint, grant_type, grant, extra)

    def _uses_json_token_request(self) -> bool:
        """Return whether this row uses the non-standard JSON token shim."""

        return getattr(self.oauth_client, "token_request_format_value", "form") == "json"

    def _authlib_token_request(
        self,
        endpoint: str,
        grant_type: str,
        grant: Mapping[str, Any],
        extra: Mapping[str, str],
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
            self._log_token_failure(exc.error, body)
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400, body=body) from exc
        except httpx.HTTPStatusError as exc:
            # Authlib raises this only for >=500 (it raise_for_status()es server errors).
            self._log_token_failure(exc.response.status_code, _response_body(exc.response))
            raise OAuthFlowError(
                TOKEN_EXCHANGE_FAILED,
                exc.response.status_code,
                body=_response_body(exc.response),
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            # Authlib parses the token body as JSON before checking the status, so a
            # non-JSON 4xx (the documented Anthropic/CDN 403/429 block page) surfaces as
            # a ValueError, and a transport failure as httpx.HTTPError — both map to the
            # stable OAuthFlowError seam, like the JSON-shim and _get_json paths.
            self._log_token_failure("transport_error", {"error": str(exc)})
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc
        finally:
            session.close()
        return _token_material(dict(token))

    def _json_token_request(
        self,
        endpoint: str,
        grant_type: str,
        grant: Mapping[str, Any],
        extra: Mapping[str, str],
    ) -> dict[str, Any]:
        """POST a non-standard JSON token body (provider quirk Authlib does not emit)."""

        body: dict[str, Any] = {
            **extra,
            "client_id": str(getattr(self.oauth_client, "client_id", "")),
            "grant_type": grant_type,
            **grant,
        }
        client_secret = str(getattr(self.oauth_client, "client_secret", "") or "")
        if client_secret:
            body["client_secret"] = client_secret
        client = self._httpx_client()
        try:
            response = client.post(endpoint, json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            self._log_token_failure(exc.response.status_code, _response_body(exc.response))
            raise OAuthFlowError(
                TOKEN_EXCHANGE_FAILED,
                exc.response.status_code,
                body=_response_body(exc.response),
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc
        finally:
            client.close()
        if not isinstance(data, dict):
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)
        return _token_material(data)

    def _session(self) -> OAuth2Client:
        """Build the per-row Authlib OAuth2 client.

        Client authentication rides the body (``client_secret_post``) to match the
        row's stored secret; a public client (no secret) sends only ``client_id``.
        Scope is omitted so refresh requests stay minimal — scopes are owned by the
        authorize URL. Authlib auto-refresh is left off so a rotated refresh token
        cannot be replayed behind ``Credential`` refresh locking.
        """

        secret = str(getattr(self.oauth_client, "client_secret", "") or "")
        return OAuth2Client(
            client_id=str(getattr(self.oauth_client, "client_id", "")),
            client_secret=secret or None,
            token_endpoint_auth_method="client_secret_post" if secret else "none",
            headers={"User-Agent": USER_AGENT},
            **_outbound_kwargs(self._transport),
        )

    def _httpx_client(self) -> httpx.Client:
        """Return a plain httpx client carrying the honest UA (JSON-shim transport)."""

        return httpx.Client(headers={"User-Agent": USER_AGENT}, **_outbound_kwargs(self._transport))

    def _log_token_failure(self, status: object, body: Any) -> None:
        """Log a redacted token-request failure against the client label."""

        client_label = str(
            getattr(self.oauth_client, "slug", "") or getattr(self.oauth_client, "client_id", "") or "unknown"
        )
        logger.warning(
            "OAuth token request failed for %s: status=%s body=%r",
            client_label,
            status,
            _safe_error_body(body),
        )

    def _authorize_query(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: Iterable[str],
        code_challenge: str | None,
    ) -> dict[str, str]:
        """Return the shared authorize query parameters (OIDC adds ``nonce``/``openid``)."""

        query: dict[str, str] = {
            **_param_values(self.oauth_client, "authorize_param_values"),
            "client_id": str(getattr(self.oauth_client, "client_id", "")),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
        }
        if getattr(self.oauth_client, "supports_pkce", False) and code_challenge:
            query["code_challenge"] = code_challenge
            query["code_challenge_method"] = "S256"
        return query

    def _endpoint(self, field: str) -> str:
        """Return a configured endpoint value, or raise when the row omits it."""

        value = str(getattr(self.oauth_client, field, "") or "")
        if not value:
            self.ensure_endpoints()
            value = str(getattr(self.oauth_client, field, "") or "")
        if not value:
            raise OAuthFlowError(MISSING_ENDPOINT, 400)
        return value


def _outbound_kwargs(transport: httpx.BaseTransport | None) -> dict[str, Any]:
    """Shared outbound httpx policy: a request timeout and the transport.

    A test injects an ``httpx.MockTransport``; a real call rides the integrate
    addon's SSRF-pinned ``PinnedTransport``, so OAuth, discovery, and userinfo get
    the same address pinning and system-store TLS as every other outbound call.
    ``allow_private=True`` permits operator-configured self-hosted IDPs on private
    networks while still rejecting cloud metadata and the other SSRF escapes.
    """

    return {
        "timeout": HTTP_TIMEOUT_SECONDS,
        "transport": transport if transport is not None else PinnedTransport(allow_private=True),
    }


def _param_values(oauth_client: object, property_name: str) -> dict[str, str]:
    """Return provider-specific params exposed by an OAuth client property."""

    value = getattr(oauth_client, property_name, {})
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items() if item is not None}
    return {}


def with_query(url: str, query: Mapping[str, str]) -> str:
    """Return ``url`` with ``query`` appended or merged."""

    parts = parse.urlsplit(url)
    existing = parse.parse_qsl(parts.query, keep_blank_values=True)
    encoded = parse.urlencode([*existing, *query.items()])
    return parse.urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))


def _get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    error_code: str,
    _transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """GET a JSON document over httpx with the honest User-Agent."""

    request_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        **dict(headers or {}),
    }
    try:
        with httpx.Client(**_outbound_kwargs(_transport)) as client:
            response = client.get(url, headers=request_headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthFlowError(error_code, 400) from exc
    if not isinstance(data, dict):
        raise OAuthFlowError(error_code, 400)
    return data


def _token_material(response: Mapping[str, Any]) -> dict[str, Any]:
    """Return the carried token keys from a token response, requiring an access token."""

    tokens = {key: response[key] for key in _TOKEN_MATERIAL_KEYS if key in response}
    if not tokens.get("access_token"):
        raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)
    return tokens


def _response_body(response: httpx.Response) -> Any:
    """Return a JSON or text response body from an httpx response."""

    try:
        return response.json()
    except ValueError:
        return response.text


def _safe_error_body(value: Any) -> Any:
    """Return a provider error body with obvious credential fields redacted."""

    if isinstance(value, Mapping):
        redacted_keys = {
            "access_token",
            "refresh_token",
            "id_token",
            "token",
            "code",
            "code_verifier",
            "client_secret",
        }
        return {
            str(key): "[redacted]" if str(key).lower() in redacted_keys else _safe_error_body(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_error_body(item) for item in value]
    return value
