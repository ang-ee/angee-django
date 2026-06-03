"""Stateless OIDC protocol helpers for IAM OAuth client rows."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib import error, parse, request

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError

from angee.iam.oidc.errors import (
    DISCOVERY_FAILED,
    INVALID_ID_TOKEN,
    MISSING_ENDPOINT,
    TOKEN_EXCHANGE_FAILED,
    USERINFO_FAILED,
    OidcFlowError,
)

HTTP_TIMEOUT_SECONDS = 10
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_DISCOVERY_FIELDS = {
    "issuer": "issuer",
    "authorize_endpoint": "authorization_endpoint",
    "token_endpoint": "token_endpoint",
    "revoke_endpoint": "revocation_endpoint",
    "userinfo_endpoint": "userinfo_endpoint",
    "jwks_uri": "jwks_uri",
}
_ALLOWED_JWT_ALGORITHMS = (
    "RS256",
    "ES256",
)


def fetch_discovery(oauth_client: object) -> dict[str, Any]:
    """Fetch OIDC discovery and fill blank endpoint fields on ``oauth_client``."""

    discovery_url = str(getattr(oauth_client, "discovery_url", "") or "")
    if not discovery_url:
        return {}
    try:
        discovery = _get_json(discovery_url)
    except OidcFlowError:
        raise
    except Exception as exc:
        raise OidcFlowError(DISCOVERY_FAILED, 400) from exc
    for oauth_client_field, discovery_field in _DISCOVERY_FIELDS.items():
        if getattr(oauth_client, oauth_client_field, ""):
            continue
        value = discovery.get(discovery_field)
        if value:
            setattr(oauth_client, oauth_client_field, str(value))
    return discovery


def build_authorize_url(
    oauth_client: object,
    *,
    state: str,
    nonce: str,
    redirect_uri: str,
    scopes: Iterable[str],
    code_challenge: str | None = None,
) -> str:
    """Return the provider authorization URL for one OIDC code flow."""

    authorize_endpoint = _endpoint(oauth_client, "authorize_endpoint", "authorization_endpoint")
    query: dict[str, str] = {
        "client_id": str(getattr(oauth_client, "client_id", "")),
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
    }
    if getattr(oauth_client, "supports_pkce", False) and code_challenge:
        query["code_challenge"] = code_challenge
        query["code_challenge_method"] = "S256"
    return _with_query(authorize_endpoint, query)


def exchange_code(
    oauth_client: object,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for token material."""

    token_endpoint = _endpoint(oauth_client, "token_endpoint", "token_endpoint")
    payload = {
        "client_id": str(getattr(oauth_client, "client_id", "")),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    client_secret = str(getattr(oauth_client, "client_secret", "") or "")
    if client_secret:
        payload["client_secret"] = client_secret
    if getattr(oauth_client, "supports_pkce", False) and code_verifier:
        payload["code_verifier"] = code_verifier
    try:
        response = _post_form(token_endpoint, payload)
    except OidcFlowError:
        raise
    except Exception as exc:
        raise OidcFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc
    tokens = {
        key: response[key]
        for key in ("access_token", "refresh_token", "id_token", "expires_in", "scope")
        if key in response
    }
    if not tokens.get("access_token"):
        raise OidcFlowError(TOKEN_EXCHANGE_FAILED, 400)
    return tokens


def verify_id_token(
    oauth_client: object,
    id_token: str,
    *,
    nonce: str | None = None,
    _jwks_client: Any | None = None,
) -> dict[str, Any]:
    """Verify and return claims from one OIDC ID token."""

    if not id_token:
        raise OidcFlowError(INVALID_ID_TOKEN, 400)
    issuer = str(getattr(oauth_client, "issuer", "") or "")
    jwks_uri = str(getattr(oauth_client, "jwks_uri", "") or "")
    if not issuer or not jwks_uri:
        fetch_discovery(oauth_client)
        issuer = str(getattr(oauth_client, "issuer", "") or "")
        jwks_uri = str(getattr(oauth_client, "jwks_uri", "") or "")
    if not issuer or not jwks_uri:
        raise OidcFlowError(MISSING_ENDPOINT, 400)
    try:
        jwks_client = _jwks_client or PyJWKClient(
            jwks_uri,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=_ALLOWED_JWT_ALGORITHMS,
            audience=str(getattr(oauth_client, "client_id", "")),
            issuer=issuer,
            options={"require": ["exp", "iat"], "verify_exp": True},
        )
    except (PyJWKClientError, PyJWTError, ValueError, TypeError) as exc:
        raise OidcFlowError(INVALID_ID_TOKEN, 400) from exc
    if not isinstance(claims, dict):
        raise OidcFlowError(INVALID_ID_TOKEN, 400)
    if claims.get("iss") != issuer:
        raise OidcFlowError(INVALID_ID_TOKEN, 400)
    if not _audience_matches(claims.get("aud"), str(getattr(oauth_client, "client_id", ""))):
        raise OidcFlowError(INVALID_ID_TOKEN, 400)
    if nonce is not None and claims.get("nonce") != nonce:
        raise OidcFlowError(INVALID_ID_TOKEN, 400)
    return claims


def fetch_userinfo(oauth_client: object, access_token: str) -> dict[str, Any]:
    """Fetch userinfo claims with one OAuth access token."""

    if not access_token:
        raise OidcFlowError(USERINFO_FAILED, 400)
    userinfo_endpoint = _endpoint(oauth_client, "userinfo_endpoint", "userinfo_endpoint")
    try:
        return _get_json(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            error_code=USERINFO_FAILED,
        )
    except OidcFlowError:
        raise
    except Exception as exc:
        raise OidcFlowError(USERINFO_FAILED, 400) from exc


def revoke_token(oauth_client: object, token: str) -> None:
    """Best-effort RFC 7009 token revocation for an OAuth credential."""

    revoke_endpoint = str(getattr(oauth_client, "revoke_endpoint", "") or "")
    if not revoke_endpoint or not token:
        return
    fields = {
        "client_id": str(getattr(oauth_client, "client_id", "")),
        "token": token,
        "token_type_hint": "access_token",
    }
    client_secret = str(getattr(oauth_client, "client_secret", "") or "")
    if client_secret:
        fields["client_secret"] = client_secret
    _post_form_no_response(revoke_endpoint, fields)


def _endpoint(oauth_client: object, oauth_client_field: str, discovery_field: str) -> str:
    """Return an endpoint value, loading discovery when the OAuth client field is blank."""

    value = str(getattr(oauth_client, oauth_client_field, "") or "")
    if value:
        return value
    discovery = fetch_discovery(oauth_client)
    value = str(getattr(oauth_client, oauth_client_field, "") or discovery.get(discovery_field, "") or "")
    if not value:
        raise OidcFlowError(MISSING_ENDPOINT, 400)
    return value


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
    error_code: str = DISCOVERY_FAILED,
) -> dict[str, Any]:
    """GET a JSON document using the standard library HTTP client."""

    request_headers = {"Accept": "application/json", **dict(headers or {})}
    req = request.Request(url, headers=request_headers, method="GET")
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return _loads_json(response.read(), error_code=error_code)
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OidcFlowError(error_code, 400) from exc


def _post_form(url: str, fields: Mapping[str, str]) -> dict[str, Any]:
    """POST a form body and return the JSON response."""

    data = parse.urlencode(fields).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return _loads_json(response.read(), error_code=TOKEN_EXCHANGE_FAILED)
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OidcFlowError(TOKEN_EXCHANGE_FAILED, 400) from exc


def _post_form_no_response(url: str, fields: Mapping[str, str]) -> None:
    """POST a form body when the endpoint may return an empty response."""

    data = parse.urlencode(fields).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _BROWSER_USER_AGENT,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
        response.read()


def _loads_json(payload: bytes, *, error_code: str) -> dict[str, Any]:
    """Decode a JSON object response."""

    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise OidcFlowError(error_code, 400)
    return decoded


def _audience_matches(value: object, expected: str) -> bool:
    """Return whether an OIDC ``aud`` claim contains ``expected``."""

    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in value
    return False
