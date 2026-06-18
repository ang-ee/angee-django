"""Stateless OAuth2 protocol bound to one ``OAuthClient`` registration row.

The base of the connection protocol: authorization-code redirect, code exchange,
refresh, and revocation — everything needed to connect an external account for
API access (Gemini, Grok, Anthropic), with no identity/login concern. OIDC login
extends this in ``angee.iam_integrate_oidc.protocol.OAuthClientOidcProtocol``.

Endpoints are taken from the row as configured; when a row has a discovery URL,
the protocol asks the row to fill missing OAuth endpoints before failing. OIDC
login extends this in ``iam_integrate_oidc`` for ID-token/userinfo verification.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any
from urllib import error, parse, request

from angee.integrate.oauth.errors import (
    MISSING_ENDPOINT,
    TOKEN_EXCHANGE_FAILED,
    USERINFO_FAILED,
    OAuthFlowError,
)

HTTP_TIMEOUT_SECONDS = 10
# An honest, non-browser User-Agent for all outbound connection requests. Must NOT
# spoof a browser: Anthropic's edge denylists browser/curl User-Agents with a 429
# ``rate_limit_error`` (and blocks urllib's ``Python-urllib`` default with a 403),
# while an honest client UA passes. See docs/backend/guidelines.md (Pitfalls).
_USER_AGENT = "Angee-Integrate/1.0"
logger = logging.getLogger(__name__)


class OAuthClientProtocol:
    """OAuth2 authorization-code + refresh protocol for one ``OAuthClient`` row."""

    def __init__(self, oauth_client: Any) -> None:
        """Bind the protocol to one OAuth client registration row."""

        self.oauth_client = oauth_client

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
        return _with_query(self._endpoint("authorize_endpoint"), query)

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Exchange an authorization code for token material."""

        grant: dict[str, Any] = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if state is not None:
            grant["state"] = state
        if getattr(self.oauth_client, "supports_pkce", False) and code_verifier:
            grant["code_verifier"] = code_verifier
        return self._token_request(grant)

    def refresh_token(self, *, refresh_token: str) -> dict[str, Any]:
        """Exchange a stored refresh token for fresh token material (RFC 6749 §6).

        The provider may rotate the refresh token; when it returns a new one the
        caller persists it. Raises ``OAuthFlowError`` when the grant is rejected.
        """

        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

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
            )
        except Exception:
            return {}

    def revoke_token(self, token: str) -> None:
        """Best-effort RFC 7009 token revocation for an OAuth credential."""

        revoke_endpoint = str(getattr(self.oauth_client, "revoke_endpoint", "") or "")
        if not revoke_endpoint or not token:
            return
        fields = {
            "client_id": str(getattr(self.oauth_client, "client_id", "")),
            "token": token,
            "token_type_hint": "access_token",
        }
        client_secret = str(getattr(self.oauth_client, "client_secret", "") or "")
        if client_secret:
            fields["client_secret"] = client_secret
        _post_form_no_response(revoke_endpoint, fields)

    def ensure_endpoints(self) -> dict[str, Any]:
        """Ask the OAuth client row to fill endpoint fields from discovery."""

        discover = getattr(self.oauth_client, "discover_endpoints", None)
        if not callable(discover):
            return {}
        return dict(discover())

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

    def _token_request(self, grant: Mapping[str, Any]) -> dict[str, Any]:
        """POST one grant to the token endpoint and return validated token material.

        The grant-specific fields (``code``/``refresh_token``/…) ride ``grant``; the
        client id, optional secret, and provider ``token_param_values`` are common to
        every grant. The ``OAuthClient`` row owns the body format via
        ``token_request_format_value``.
        """

        token_endpoint = self._endpoint("token_endpoint")
        payload: dict[str, Any] = {
            **_param_values(self.oauth_client, "token_param_values"),
            "client_id": str(getattr(self.oauth_client, "client_id", "")),
            **grant,
        }
        client_secret = str(getattr(self.oauth_client, "client_secret", "") or "")
        if client_secret:
            payload["client_secret"] = client_secret
        try:
            if getattr(self.oauth_client, "token_request_format_value", "form") == "json":
                response = _post_json(token_endpoint, payload)
            else:
                response = _post_form(token_endpoint, payload)
        except OAuthFlowError as exc:
            if exc.code == TOKEN_EXCHANGE_FAILED:
                client_label = str(
                    getattr(self.oauth_client, "slug", "")
                    or getattr(self.oauth_client, "client_id", "")
                    or "unknown"
                )
                logger.warning(
                    "OAuth token request failed for %s: status=%s body=%r",
                    client_label,
                    exc.http_status,
                    _safe_error_body(exc.body),
                )
            raise
        except Exception as exc:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc
        tokens = {
            key: response[key]
            for key in ("access_token", "refresh_token", "id_token", "expires_in", "scope")
            if key in response
        }
        if not tokens.get("access_token"):
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)
        return tokens

    def _endpoint(self, field: str) -> str:
        """Return a configured endpoint value, or raise when the row omits it."""

        value = str(getattr(self.oauth_client, field, "") or "")
        if not value:
            self.ensure_endpoints()
            value = str(getattr(self.oauth_client, field, "") or "")
        if not value:
            raise OAuthFlowError(MISSING_ENDPOINT, 400)
        return value


def _param_values(oauth_client: object, property_name: str) -> dict[str, str]:
    """Return provider-specific params exposed by an OAuth client property."""

    value = getattr(oauth_client, property_name, {})
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items() if item is not None}
    return {}


def _with_query(url: str, query: Mapping[str, str]) -> str:
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
) -> dict[str, Any]:
    """GET a JSON document using the standard library HTTP client."""

    request_headers = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        **dict(headers or {}),
    }
    req = request.Request(url, headers=request_headers, method="GET")
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return _loads_json(response.read(), error_code=error_code)
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OAuthFlowError(error_code, 400) from exc


def _post_form(url: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """POST a form body and return the JSON response."""

    data = parse.urlencode(fields).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return _loads_json(response.read(), error_code=TOKEN_EXCHANGE_FAILED)
    except error.HTTPError as exc:
        raise OAuthFlowError(
            TOKEN_EXCHANGE_FAILED,
            exc.code,
            body=_http_error_body(exc),
        ) from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc


def _post_json(url: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """POST a JSON body and return the JSON response."""

    data = json.dumps(dict(fields)).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return _loads_json(response.read(), error_code=TOKEN_EXCHANGE_FAILED)
    except error.HTTPError as exc:
        raise OAuthFlowError(
            TOKEN_EXCHANGE_FAILED,
            exc.code,
            body=_http_error_body(exc),
        ) from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc


def _post_form_no_response(url: str, fields: Mapping[str, str]) -> None:
    """POST a form body when the endpoint may return an empty response."""

    data = parse.urlencode(fields).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
        response.read()


def _loads_json(payload: bytes, *, error_code: str) -> dict[str, Any]:
    """Decode a JSON object response."""

    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise OAuthFlowError(error_code, 400)
    return decoded


def _http_error_body(exc: error.HTTPError) -> Any:
    """Return a JSON or text response body from an HTTP error."""

    payload = exc.read()
    if not payload:
        return ""
    text = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


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
            str(key): "[redacted]"
            if str(key).lower() in redacted_keys
            else _safe_error_body(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_error_body(item) for item in value]
    return value
