"""Tests for the OAuth/OIDC protocol, connect flow, and OIDC login resolution."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from urllib import parse

import httpx2
import pytest
import yaml
from asgiref.sync import async_to_sync, sync_to_async
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, override_settings
from django.utils import timezone
from rebac import system_context
from strawberry_django_aggregates import AggregateOp, compute_aggregation

from angee.graphql.schema import GraphQLSchemas
from angee.iam import schema as iam_schema
from angee.iam_integrate_oidc import identity
from angee.iam_integrate_oidc import protocol as oidc_protocol
from angee.iam_integrate_oidc import schema as oidc_schema
from angee.iam_integrate_oidc.errors import IDENTITY_RESOLUTION_FAILED, IdentityFlowError
from angee.iam_integrate_oidc.models import OAuthClientOidc
from angee.iam_integrate_oidc.protocol import OAuthClientOidcProtocol
from angee.iam_integrate_oidc.providers import GoogleType
from angee.integrate import schema as integrate_schema
from angee.integrate.connect import complete_account_connect
from angee.integrate.errors import IntegrationError
from angee.integrate.http import _PinnedBackend
from angee.integrate.models import AccountStatus, CredentialStatus
from angee.integrate.oauth import client as oauth_protocol
from angee.integrate.oauth import discovery as oauth_discovery
from angee.integrate.oauth import state as oauth_state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import (
    DISCOVERY_FAILED,
    EXTERNAL_ACCOUNT_RESOLUTION_FAILED,
    INVALID_ID_TOKEN,
    INVALID_STATE,
    TOKEN_EXCHANGE_FAILED,
    OAuthFlowError,
)
from angee.messaging.testing.models import Handle as PartiesHandle
from tests.conftest import (
    Credential,
    ExternalAccount,
    OAuthClient,
    SchemaAddon,
    execute_schema,
    result_data,
)
from tests.test_parties_graphql import Person as PartiesPerson

# OIDC first login uses the same concrete Person and handle models as parties tests.


def test_discovery_fallback_fills_blank_authorize_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank authorization endpoint is loaded from discovery."""

    calls: list[str] = []
    oauth_client = _stub_oauth_client(authorize_endpoint="")
    oidc = _stub_login_oauth_client(
        oauth_client=oauth_client,
        discovery_url="https://issuer.example/.well-known/openid-configuration",
    )

    def get_json(url: str, *, headers: dict[str, str] | None = None, error_code: str | None = None) -> dict[str, Any]:
        del headers, error_code
        calls.append(url)
        return {"authorization_endpoint": "https://issuer.example/oauth/authorize"}

    monkeypatch.setattr(oauth_discovery, "_get_json", get_json)

    url = OAuthClientOidcProtocol(oidc).authorize_url(
        state="state-token",
        nonce="nonce-token",
        redirect_uri="https://app.example/callback",
        scopes=("openid", "email"),
    )

    assert calls == ["https://issuer.example/.well-known/openid-configuration"]
    assert oauth_client.authorize_endpoint == "https://issuer.example/oauth/authorize"
    assert url.startswith("https://issuer.example/oauth/authorize?")


def test_ensure_endpoints_caches_document_by_discovery_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery cache hits avoid a second discovery document fetch."""

    discovery_url = "https://cached.example/.well-known/openid-configuration"
    oauth_client = _stub_oauth_client(authorize_endpoint="", token_endpoint="", userinfo_endpoint="")
    oidc = _stub_login_oauth_client(oauth_client=oauth_client, issuer="", jwks_uri="", discovery_url=discovery_url)
    cached_documents: dict[str, dict[str, Any]] = {}
    cache_gets: list[str] = []
    cache_sets: list[tuple[str, int | None]] = []
    fetches: list[str] = []

    def cache_get(key: str) -> dict[str, Any] | None:
        cache_gets.append(key)
        return cached_documents.get(key)

    def cache_set(key: str, value: dict[str, Any], timeout: int | None = None) -> None:
        cache_sets.append((key, timeout))
        cached_documents[key] = value

    def get_json(url: str, *, headers: dict[str, str] | None = None, error_code: str | None = None) -> dict[str, Any]:
        del headers, error_code
        fetches.append(url)
        return {
            "issuer": "https://cached.example",
            "authorization_endpoint": "https://cached.example/oauth/authorize",
            "token_endpoint": "https://cached.example/oauth/token",
            "userinfo_endpoint": "https://cached.example/oauth/userinfo",
            "jwks_uri": "https://cached.example/oauth/jwks",
        }

    monkeypatch.setattr(oauth_discovery.cache, "get", cache_get)
    monkeypatch.setattr(oauth_discovery.cache, "set", cache_set)
    monkeypatch.setattr(oauth_discovery, "_get_json", get_json)

    protocol = OAuthClientOidcProtocol(oidc)
    first = protocol.ensure_endpoints()
    second = protocol.ensure_endpoints()

    assert first == second
    assert fetches == [discovery_url]
    assert len(cache_gets) == 2
    assert len(cache_sets) == 1
    assert cache_sets[0][1] == 3600


def test_outbound_requests_send_honest_user_agent() -> None:
    """Outbound requests send an honest, non-browser User-Agent.

    Not the HTTP client's default (Anthropic's edge 403s ``python-httpx``/``Python-urllib``)
    and not a spoofed browser/curl UA (Anthropic 429s those) — an honest client UA passes.
    The GET helper (discovery/userinfo) and the Authlib token POST both carry it.
    """

    seen: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx2.Response(200, json={"access_token": "access-token"})

    transport = httpx2.MockTransport(handler)

    oauth_protocol._get_json(
        "https://idp.example/.well-known/openid-configuration",
        error_code=DISCOVERY_FAILED,
        _transport=transport,
    )
    protocol = OAuthClientProtocol(_stub_oauth_client())
    protocol._transport = transport
    protocol.exchange_code(code="abc", redirect_uri="https://app.example/callback")

    assert seen == [oauth_protocol.USER_AGENT, oauth_protocol.USER_AGENT]
    # Lock in the pitfall: never an HTTP-client default or a spoofed browser/curl UA.
    sent = oauth_protocol.USER_AGENT.lower()
    assert not any(token in sent for token in ("python-urllib", "python-httpx", "mozilla", "chrome", "curl"))


def test_authorize_url_contains_state_nonce_and_pkce() -> None:
    """OIDC authorize URL includes state, nonce, and PKCE parameters when supported."""

    oauth_client = _stub_oauth_client(supports_pkce=True)
    oidc = _stub_login_oauth_client(oauth_client=oauth_client)
    state_token, record = oauth_state.issue(oauth_client, "https://app.example/callback")
    url = OAuthClientOidcProtocol(oidc).authorize_url(
        state=state_token,
        nonce=record.nonce,
        redirect_uri="https://app.example/callback",
        scopes=("openid", "email"),
        code_challenge="challenge",
    )
    query = parse.parse_qs(parse.urlsplit(url).query)

    assert record.nonce != state_token
    assert query["state"] == [state_token]
    assert query["nonce"] == [record.nonce]
    assert query["code_challenge"] == ["challenge"]
    assert query["code_challenge_method"] == ["S256"]


def test_authorize_url_prepends_openid_when_scope_is_absent() -> None:
    """OIDC authorize URL adds the required scope when configured scopes omit it."""

    url = OAuthClientOidcProtocol(_stub_login_oauth_client()).authorize_url(
        state="state-token",
        nonce="nonce-token",
        redirect_uri="https://app.example/callback",
        scopes=("email", "profile"),
    )
    query = parse.parse_qs(parse.urlsplit(url).query)

    assert query["scope"] == ["openid email profile"]


def test_oauth_authorize_url_omits_oidc_nonce_and_honors_extra_params() -> None:
    """Plain OAuth account-connect URLs do not add OIDC-only parameters."""

    oauth_client = _stub_oauth_client(
        supports_pkce=True,
        authorize_param_values={"audience": "https://api.example"},
    )
    url = OAuthClientProtocol(oauth_client).authorize_url(
        state="state-token",
        redirect_uri="https://app.example/oauth/callback",
        scopes=("offline", "email"),
        code_challenge="challenge",
    )
    query = parse.parse_qs(parse.urlsplit(url).query)

    assert "nonce" not in query
    assert query["scope"] == ["offline email"]
    assert query["audience"] == ["https://api.example"]
    assert query["code_challenge"] == ["challenge"]
    assert query["code_challenge_method"] == ["S256"]


def test_exchange_code_posts_json_body_with_pkce() -> None:
    """OAuth clients can opt into the JSON token exchange provider quirk."""

    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["fields"] = json.loads(request.content)
        captured["ua"] = request.headers.get("user-agent")
        return httpx2.Response(
            200,
            json={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "account": {
                    "uuid": "account-uuid",
                    "email_address": "person@example.com",
                },
                "ignored": "not-token-material",
            },
        )

    oauth_client = _stub_oauth_client(
        supports_pkce=True,
        token_request_format="json",
        token_param_values={"audience": "https://api.example", "expires_in": 31536000},
    )
    protocol = OAuthClientProtocol(oauth_client)
    protocol._transport = httpx2.MockTransport(handler)

    tokens = protocol.exchange_code(
        code="auth-code",
        redirect_uri="https://app.example/callback",
        code_verifier="verifier",
        state="state-token",
    )

    assert tokens == {"access_token": "access-token", "refresh_token": "refresh-token"}
    assert protocol.token_response_claims == {
        "account": {
            "uuid": "account-uuid",
            "email_address": "person@example.com",
        },
    }
    assert captured["url"] == "https://issuer.example/oauth/token"
    assert captured["ua"] == oauth_protocol.USER_AGENT
    assert captured["fields"] == {
        "audience": "https://api.example",
        "client_id": "oidc-client",
        "client_secret": "secret",
        "code": "auth-code",
        "code_verifier": "verifier",
        "expires_in": 31536000,
        "grant_type": "authorization_code",
        "redirect_uri": "https://app.example/callback",
        "state": "state-token",
    }


def test_refresh_token_posts_refresh_grant() -> None:
    """`refresh_token` posts a ``refresh_token`` grant and returns the renewed material."""

    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["fields"] = json.loads(request.content)
        return httpx2.Response(200, json={"access_token": "new-access", "refresh_token": "rotated", "expires_in": 7200})

    oauth_client = _stub_oauth_client(
        token_request_format="json",
        token_param_values={"audience": "https://api.example", "expires_in": 31536000},
    )
    protocol = OAuthClientProtocol(oauth_client)
    protocol._transport = httpx2.MockTransport(handler)

    tokens = protocol.refresh_token(refresh_token="stored-refresh")

    assert tokens == {"access_token": "new-access", "refresh_token": "rotated", "expires_in": 7200}
    assert captured["url"] == "https://issuer.example/oauth/token"
    assert captured["fields"] == {
        "audience": "https://api.example",
        "client_id": "oidc-client",
        "client_secret": "secret",
        "grant_type": "refresh_token",
        "refresh_token": "stored-refresh",
    }


@pytest.mark.parametrize("request_format", ("json", "form"))
@pytest.mark.parametrize("grant", ("authorization_code", "refresh_token"))
def test_fixed_public_client_never_reads_encrypted_secret(request_format: str, grant: str) -> None:
    """A fixed public PKCE client exchanges every grant without secret access."""

    captured: dict[str, Any] = {}

    class PublicClient:
        is_public_client = OAuthClient.is_public_client
        slug = "public-client"
        client_id = "public-client-id"
        token_endpoint = "https://issuer.example/oauth/token"
        token_request_format_value = request_format
        token_param_values: dict[str, Any] = {}
        supports_pkce = True
        manual_redirect_uri = "https://issuer.example/oauth/code/callback"

        @property
        def client_secret(self) -> str:
            raise ImproperlyConfigured("encrypted secret must remain unread")

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["fields"] = (
            json.loads(request.content)
            if request_format == "json"
            else dict(parse.parse_qsl(request.content.decode()))
        )
        captured["authorization"] = request.headers.get("authorization")
        return httpx2.Response(200, json={"access_token": "synthetic-access"})

    protocol = OAuthClientProtocol(PublicClient())
    protocol._transport = httpx2.MockTransport(handler)

    if grant == "authorization_code":
        result = protocol.exchange_code(
            code="synthetic-code",
            redirect_uri="https://app.example/callback",
            code_verifier="synthetic-verifier",
            state="synthetic-state",
        )
        assert captured["fields"]["code_verifier"] == "synthetic-verifier"
    else:
        result = protocol.refresh_token(refresh_token="synthetic-refresh")

    assert result["access_token"] == "synthetic-access"
    assert captured["fields"]["client_id"] == "public-client-id"
    assert "client_secret" not in captured["fields"]
    assert captured["authorization"] is None


def test_confidential_json_client_keeps_unreadable_secret_fail_closed() -> None:
    """A confidential client still fails before exchange when its secret is unreadable."""

    class ConfidentialClient:
        is_public_client = OAuthClient.is_public_client
        slug = "confidential-client"
        client_id = "confidential-client-id"
        token_endpoint = "https://issuer.example/oauth/token"
        token_request_format_value = "json"
        token_param_values: dict[str, Any] = {}
        supports_pkce = True
        manual_redirect_uri = ""

        @property
        def client_secret(self) -> str:
            raise ImproperlyConfigured("synthetic unreadable secret")

    with pytest.raises(ImproperlyConfigured, match="synthetic unreadable secret"):
        OAuthClientProtocol(ConfidentialClient()).exchange_code(
            code="synthetic-code",
            redirect_uri="https://app.example/callback",
            code_verifier="synthetic-verifier",
            state="synthetic-state",
        )


def test_exchange_code_form_path_maps_non_json_error_to_oauth_flow_error() -> None:
    """A non-JSON token error on the default form path surfaces as ``OAuthFlowError``.

    Authlib parses the token body as JSON before checking the status, so a provider/CDN
    edge that answers a non-JSON 4xx (the documented Anthropic 403/429 block page) raises
    a ``ValueError`` inside Authlib; it must map to the stable seam, not leak a 500.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, text="<html>403 Forbidden</html>")

    protocol = OAuthClientProtocol(_stub_oauth_client())
    protocol._transport = httpx2.MockTransport(handler)

    with pytest.raises(OAuthFlowError) as exc_info:
        protocol.exchange_code(code="auth-code", redirect_uri="https://app.example/callback")

    assert exc_info.value.code == TOKEN_EXCHANGE_FAILED


def test_token_transport_error_log_omits_request_url_and_client_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Token transport telemetry contains a class and slug, without request metadata."""

    canary_url = "https://issuer.example/oauth/token?code=canary-code"

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("failed " + canary_url, request=request)

    oauth_client = _stub_oauth_client(token_endpoint=canary_url)
    protocol = OAuthClientProtocol(oauth_client)
    protocol._transport = httpx2.MockTransport(handler)

    with pytest.raises(OAuthFlowError):
        protocol.exchange_code(code="canary-code", redirect_uri="https://app.example/callback")

    assert "ConnectError" in caplog.text
    assert "canary-code" not in caplog.text
    assert oauth_client.client_id not in caplog.text


def test_refresh_token_form_path_returns_renewed_material() -> None:
    """The default (form) refresh path returns the filtered token material."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600})

    protocol = OAuthClientProtocol(_stub_oauth_client())
    protocol._transport = httpx2.MockTransport(handler)

    tokens = protocol.refresh_token(refresh_token="stored-refresh")

    assert tokens == {"access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600}


@pytest.mark.parametrize("request_format", ["form", "json"])
def test_exchange_code_public_client_posts_client_id_and_verifier(request_format: str) -> None:
    """A public client (no secret) still posts ``client_id`` and the PKCE ``code_verifier``
    (RFC 7636) and never a ``client_secret``."""

    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = (
            json.loads(request.content) if request_format == "json" else dict(parse.parse_qsl(request.content.decode()))
        )
        assert "authorization" not in request.headers
        return httpx2.Response(200, json={"access_token": "access-token"})

    protocol = OAuthClientProtocol(_stub_oauth_client(
        client_secret="", supports_pkce=True, token_request_format=request_format,
    ))
    protocol._transport = httpx2.MockTransport(handler)

    protocol.exchange_code(
        code="auth-code",
        redirect_uri="https://app.example/callback",
        code_verifier="verifier",
        state="state-token",
    )

    body = captured["body"]
    assert body["client_id"] == "oidc-client"
    assert body["code_verifier"] == "verifier"
    assert body["grant_type"] == "authorization_code"
    assert body.get("state") == ("state-token" if request_format == "json" else None)
    assert "client_secret" not in body


@pytest.fixture
def loopback_provider() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Serve an OAuth provider on loopback; yield its base URL and the requests it received.

    The token endpoint answers form and JSON grants, userinfo answers a bearer GET and
    revocation acknowledges. Each request is recorded with its method, path, outbound
    headers and decoded fields.
    """

    received: list[dict[str, Any]] = []

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._record({})
            self._reply({"sub": "loopback-subject"})

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            is_json = self.headers.get_content_type() == "application/json"
            fields = json.loads(raw) if is_json else dict(parse.parse_qsl(raw.decode()))
            self._record(fields)
            if self.path == "/oauth/revoke":
                self._reply({})
                return
            renewed = fields["grant_type"] == "refresh_token"
            self._reply({
                "access_token": "renewed-access" if renewed else "issued-access",
                "refresh_token": "rotated-refresh" if renewed else "issued-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
            })

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _record(self, fields: dict[str, Any]) -> None:
            received.append({
                "method": self.command,
                "path": self.path,
                "user_agent": self.headers["User-Agent"],
                "authorization": self.headers["Authorization"],
                "fields": fields,
            })

        def _reply(self, document: dict[str, Any]) -> None:
            body = json.dumps(document).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("token_request_format", ("form", "json"))
def test_oauth_protocol_round_trips_over_the_real_pinned_transport(
    loopback_provider: tuple[str, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    token_request_format: str,
) -> None:
    """Every OAuth call reaches a live provider through Authlib and the real SSRF pin.

    Regression: Authlib builds its OAuth2 client on httpx2, and a transport from another
    HTTP stack fails on its first request, so sign-in code exchange and refresh broke.
    Only the provider is a test double; the Authlib session, the JSON shim, the GET
    helper, ``PinnedTransport``, its pinned backend (``allow_private`` admits loopback)
    and the sockets are the production path.
    """

    base_url, received = loopback_provider
    dialled: list[str] = []
    connect_tcp = _PinnedBackend.connect_tcp

    def record_dial(self: _PinnedBackend, host: str, port: int, **kwargs: Any) -> Any:
        dialled.append(host)
        return connect_tcp(self, host, port, **kwargs)

    monkeypatch.setattr(_PinnedBackend, "connect_tcp", record_dial)
    protocol = OAuthClientProtocol(
        _stub_oauth_client(
            token_endpoint=f"{base_url}/oauth/token",
            userinfo_endpoint=f"{base_url}/oauth/userinfo",
            revoke_endpoint=f"{base_url}/oauth/revoke",
            token_request_format=token_request_format,
        )
    )

    issued = protocol.exchange_code(code="synthetic-code", redirect_uri="https://app.example/callback")
    renewed = protocol.refresh_token(refresh_token=issued["refresh_token"])
    claims = protocol.fetch_userinfo(renewed["access_token"])
    protocol.revoke_token(renewed["access_token"])

    assert issued == {"access_token": "issued-access", "refresh_token": "issued-refresh", "expires_in": 3600}
    assert renewed == {"access_token": "renewed-access", "refresh_token": "rotated-refresh", "expires_in": 3600}
    assert claims == {"sub": "loopback-subject"}
    assert [(request["method"], request["path"]) for request in received] == [
        ("POST", "/oauth/token"),
        ("POST", "/oauth/token"),
        ("GET", "/oauth/userinfo"),
        ("POST", "/oauth/revoke"),
    ]
    assert received[0]["fields"]["grant_type"] == "authorization_code"
    assert received[0]["fields"]["code"] == "synthetic-code"
    assert received[1]["fields"]["grant_type"] == "refresh_token"
    assert received[1]["fields"]["refresh_token"] == "issued-refresh"
    assert received[2]["authorization"] == "Bearer renewed-access"
    assert received[3]["fields"]["token"] == "renewed-access"
    assert {request["user_agent"] for request in received} == {oauth_protocol.USER_AGENT}
    assert dialled == ["127.0.0.1"] * 4


def test_verify_id_token_rejects_bad_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched issuer claim."""

    oidc = _stub_login_oauth_client()
    monkeypatch.setattr(
        oidc_protocol.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "https://wrong.example",
            "aud": oidc.oauth_client.client_id,
            "nonce": "nonce",
        },
    )

    with pytest.raises(OAuthFlowError) as exc_info:
        OAuthClientOidcProtocol(oidc).verify_id_token("token", nonce="nonce", jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_rejects_bad_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched audience claim."""

    oidc = _stub_login_oauth_client()
    monkeypatch.setattr(
        oidc_protocol.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": oidc.issuer, "aud": "other-client", "nonce": "nonce"},
    )

    with pytest.raises(OAuthFlowError) as exc_info:
        OAuthClientOidcProtocol(oidc).verify_id_token("token", nonce="nonce", jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_rejects_bad_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched nonce claim."""

    oidc = _stub_login_oauth_client()
    monkeypatch.setattr(
        oidc_protocol.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": oidc.issuer, "aud": oidc.oauth_client.client_id, "nonce": "wrong"},
    )

    with pytest.raises(OAuthFlowError) as exc_info:
        OAuthClientOidcProtocol(oidc).verify_id_token("token", nonce="nonce", jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_accepts_rs256_and_rejects_ps256() -> None:
    """ID token verification accepts only the configured asymmetric algorithms."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    oidc = _stub_login_oauth_client()
    now = int(time.time())
    claims = {
        "aud": oidc.oauth_client.client_id,
        "exp": now + 60,
        "iat": now,
        "iss": oidc.issuer,
        "nonce": "nonce",
        "sub": "sub-alg",
    }
    rs256_token = oidc_protocol.jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "kid"})
    ps256_token = oidc_protocol.jwt.encode(claims, private_key, algorithm="PS256", headers={"kid": "kid"})
    jwks_client = _FakeJwksClient(private_key.public_key())

    verified = OAuthClientOidcProtocol(oidc).verify_id_token(rs256_token, nonce="nonce", jwks_client=jwks_client)

    assert verified["sub"] == "sub-alg"
    with pytest.raises(OAuthFlowError) as exc_info:
        OAuthClientOidcProtocol(oidc).verify_id_token(ps256_token, nonce="nonce", jwks_client=jwks_client)
    assert exc_info.value.code == INVALID_ID_TOKEN


def test_jwks_client_is_cached_per_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    """OIDC verification reuses the JWKS client for one configured URI."""

    clients: list[Any] = []

    class FakeJwksClient:
        """Stub PyJWT client for cache tests."""

        def __init__(self, uri: str) -> None:
            self.uri = uri
            clients.append(self)

    monkeypatch.setattr(oidc_protocol, "_PinnedPyJWKClient", FakeJwksClient)
    oidc_protocol._jwks_client.cache_clear()
    try:
        first = oidc_protocol._jwks_client("https://issuer.example/jwks")
        second = oidc_protocol._jwks_client("https://issuer.example/jwks")
    finally:
        oidc_protocol._jwks_client.cache_clear()

    assert first is second
    assert [client.uri for client in clients] == ["https://issuer.example/jwks"]


def test_jwks_fetch_uses_pinned_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyJWT owns key parsing, while Angee owns the JWKS HTTP transport."""

    requests: list[tuple[str, dict[str, str], bool, int]] = []

    class FakeHttpClient:
        def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
            allow_private: bool,
            timeout: int,
            **kwargs: object,
        ) -> httpx2.Response:
            del kwargs
            requests.append((url, headers, allow_private, timeout))
            return httpx2.Response(200, json={"keys": []})

    monkeypatch.setattr(oidc_protocol, "HttpClient", FakeHttpClient)

    data = oidc_protocol._PinnedPyJWKClient("https://issuer.example/jwks").fetch_data()

    assert data == {"keys": []}
    assert requests == [
        (
            "https://issuer.example/jwks",
            {"Accept": "application/json", "User-Agent": oidc_protocol.USER_AGENT},
            True,
            oidc_protocol.HTTP_TIMEOUT_SECONDS,
        )
    ]


@pytest.mark.django_db(transaction=True)
def test_resolver_existing_external_account_returns_owner(
    composed_tables: None,
) -> None:
    """An existing external account resolves through its owner relationship."""

    user = get_user_model().objects.create_user(username="oidc-owner", email="owner@example.com")
    oauth_client = _oauth_client()
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-existing",
        owner=user,
        email="owner@example.com",
        identity_claims={"sub": "sub-existing"},
    )

    resolved = identity.resolve(
        oauth_client,
        sub="sub-existing",
        email="owner@example.com",
        claims={"sub": "sub-existing"},
    )

    assert resolved.pk == user.pk
    assert account.pk is not None


@pytest.mark.django_db(transaction=True)
def test_resolver_blocks_non_active_account(composed_tables: None) -> None:
    """A revoked/expired/disabled external account must not log its owner in."""

    user = get_user_model().objects.create_user(username="revoked-owner", email="rev@example.com")
    oauth_client = _oauth_client()
    ExternalAccount.objects.link(
        oauth_client,
        "sub-revoked",
        owner=user,
        email="rev@example.com",
        status=AccountStatus.REVOKED,
    )

    with pytest.raises(OAuthFlowError):
        identity.resolve(oauth_client, sub="sub-revoked", email="rev@example.com", claims={"sub": "sub-revoked"})


@pytest.mark.django_db(transaction=True)
def test_external_account_manager_coerces_status_member_names(composed_tables: None) -> None:
    """External-account status normalization lives in the manager field owner."""

    user = get_user_model().objects.create_user(username="status-owner", email="status@example.com")
    oauth_client = _oauth_client()
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-status",
        owner=user,
        email="status@example.com",
        status="REVOKED",
    )

    assert account.status == AccountStatus.REVOKED


@pytest.mark.django_db(transaction=True)
def test_resolver_blocks_inactive_user(composed_tables: None) -> None:
    """A deactivated owner must not authenticate via OIDC (parity with the password path)."""

    user = get_user_model().objects.create_user(username="inactive-owner", email="ina@example.com")
    user.is_active = False
    with system_context(reason="test.oidc.inactive_user"):
        user.save(update_fields=["is_active"])
    oauth_client = _oauth_client()
    ExternalAccount.objects.link(oauth_client, "sub-inactive", owner=user, email="ina@example.com")

    with pytest.raises(OAuthFlowError):
        identity.resolve(oauth_client, sub="sub-inactive", email="ina@example.com", claims={"sub": "sub-inactive"})


@pytest.mark.django_db(transaction=True)
def test_resolver_blocks_existing_external_account_with_service_owner(composed_tables: None) -> None:
    """An existing OIDC account cannot authenticate a service principal owner."""

    user = get_user_model().objects.create_user(
        username="service-owner",
        email="svc-owner@example.com",
        kind="service",
    )
    oauth_client = _oauth_client()
    ExternalAccount.objects.link(oauth_client, "sub-service-owner", owner=user, email="svc-owner@example.com")

    with pytest.raises(OAuthFlowError):
        identity.resolve(
            oauth_client,
            sub="sub-service-owner",
            email="svc-owner@example.com",
            claims={"sub": "sub-service-owner"},
        )


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_requires_verified_email(
    composed_tables: None,
) -> None:
    """Email-match login only trusts provider-verified email addresses."""

    get_user_model().objects.create_user(username="verify-match", email="vm@example.com")
    oauth_client = _oauth_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    with pytest.raises(OAuthFlowError):
        identity.resolve(
            oauth_client,
            sub="sub-unverified",
            email="vm@example.com",
            claims={"sub": "sub-unverified", "email": "vm@example.com"},
        )

    with system_context(reason="test oidc assertions"):
        assert not ExternalAccount.objects.filter(oauth_client=oauth_client, external_id="sub-unverified").exists()


@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_requires_verified_email(
    composed_tables: None,
) -> None:
    """Create-on-login only provisions from provider-verified email addresses."""

    oauth_client = _oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    with pytest.raises(OAuthFlowError):
        identity.resolve(
            oauth_client,
            sub="sub-unverified-new",
            email="new@example.com",
            claims={"sub": "sub-unverified-new", "email": "new@example.com"},
        )

    with system_context(reason="test oidc assertions"):
        assert not ExternalAccount.objects.filter(oauth_client=oauth_client, external_id="sub-unverified-new").exists()


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_creates_external_account(
    composed_tables: None,
) -> None:
    """Email-match login links a new external account to an existing user."""

    user = get_user_model().objects.create_user(username="email-match", email="match@example.com")
    oauth_client = _oauth_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    resolved = identity.resolve(
        oauth_client,
        sub="sub-email",
        email="match@example.com",
        claims={"sub": "sub-email", "email": "match@example.com", "email_verified": True},
    )

    assert resolved.pk == user.pk
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(oauth_client=oauth_client, external_id="sub-email")
    assert account.email == "match@example.com"


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_skips_service_accounts(
    composed_tables: None,
) -> None:
    """Email-match login must never link a service principal row."""

    get_user_model().objects.create_user(
        username="svc-email-match",
        email="match-service@example.com",
        kind="service",
    )
    oauth_client = _oauth_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    with pytest.raises(OAuthFlowError):
        identity.resolve(
            oauth_client,
            sub="sub-service-email",
            email="match-service@example.com",
            claims={"sub": "sub-service-email", "email": "match-service@example.com", "email_verified": True},
        )

    with system_context(reason="test oidc service assertions"):
        assert not ExternalAccount.objects.filter(oauth_client=oauth_client, external_id="sub-service-email").exists()


def test_oidc_email_match_fails_loud_without_person_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OIDC email-match path must use the user owner's person_for_email lookup."""

    class UserModel:
        objects = object()

    monkeypatch.setattr(identity, "get_user_model", lambda: UserModel)
    resolver = object.__new__(identity.OidcIdentityResolver)

    with pytest.raises(AttributeError, match="person_for_email"):
        resolver._find_by_email("someone@example.com")


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_rejects_ambiguous_email(
    composed_tables: None,
) -> None:
    """Legacy ambiguous email refuses login even when provisioning is enabled."""

    user_model = get_user_model()
    user_model.objects.create_user(username="email-match-a", email="dupe@example.com")
    constraint = next(
        item for item in user_model._meta.constraints if item.name == "iam_user_person_email_unique"
    )
    with connection.schema_editor() as editor:
        editor.remove_constraint(user_model, constraint)
    try:
        user_model.objects.create_user(username="email-match-b", email="DUPE@example.com")
        oauth_client = _oauth_client(
            link_on_email_match=True, create_on_login=True, allowed_email_domains=["example.com"],
        )
        account_count = user_model._base_manager.count()

        with pytest.raises(IdentityFlowError) as caught:
            identity.resolve(
                oauth_client,
                sub="sub-dupe-email",
                email="dupe@example.com",
                claims={"sub": "sub-dupe-email", "email": "dupe@example.com", "email_verified": True},
            )

        assert caught.value.code == IDENTITY_RESOLUTION_FAILED
        assert user_model._base_manager.count() == account_count
        with system_context(reason="test oidc assertions"):
            assert not ExternalAccount.objects.filter(oauth_client=oauth_client, external_id="sub-dupe-email").exists()
    finally:
        user_model._base_manager.filter(username="email-match-b").update(email="email-match-b@example.com")
        with connection.schema_editor() as editor:
            editor.add_constraint(user_model, constraint)


@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_provisions_user_and_external_account(
    composed_tables: None,
) -> None:
    """Create-on-login provisions a non-superuser user and linked account."""

    oauth_client = _oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = identity.resolve(
        oauth_client,
        sub="sub-new",
        email="new@example.com",
        claims={"sub": "sub-new", "email": "new@example.com", "name": "New User", "email_verified": True},
    )

    assert user.email == "new@example.com"
    assert user.is_superuser is False
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(oauth_client=oauth_client, external_id="sub-new")
    assert account.email == "new@example.com"
    assert account.display_name == "New User"


@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_sets_user_names_from_claims(
    composed_tables: None,
) -> None:
    """Provisioned users receive first and last names from OIDC name claims."""

    oauth_client = _oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = identity.resolve(
        oauth_client,
        sub="sub-named",
        email="named@example.com",
        claims={
            "sub": "sub-named",
            "email": "named@example.com",
            "email_verified": True,
            "given_name": "Ada",
            "family_name": "Lovelace",
        },
    )

    assert user.first_name == "Ada"
    assert user.last_name == "Lovelace"


@pytest.mark.django_db(transaction=True)
def test_async_resolver_create_on_login_provisions_user_and_external_account(
    composed_tables: None,
) -> None:
    """The ASGI-facing resolver path provisions through thread-sensitive sync ORM."""

    oauth_client = _oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    # The login flow is sync; this exercises resolve() through the thread-sensitive
    # ASGI wrapping a host would apply, without a dead async wrapper in production.
    user = async_to_sync(sync_to_async(identity.resolve, thread_sensitive=True))(
        oauth_client,
        sub="sub-async",
        email="async@example.com",
        claims={"sub": "sub-async", "email": "async@example.com", "name": "Async User", "email_verified": True},
    )

    assert user.email == "async@example.com"
    assert user.is_superuser is False
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(oauth_client=oauth_client, external_id="sub-async")
    assert account.email == "async@example.com"
    assert account.display_name == "Async User"


@pytest.mark.django_db(transaction=True)
def test_resolver_disallowed_domain_raises_403(
    composed_tables: None,
) -> None:
    """Domain policy blocks linking and provisioning."""

    oauth_client = _oauth_client(
        link_on_email_match=True,
        create_on_login=True,
        allowed_email_domains=["allowed.example"],
    )

    with pytest.raises(OAuthFlowError) as exc_info:
        identity.resolve(
            oauth_client,
            sub="sub-blocked",
            email="blocked@example.com",
            claims={"sub": "sub-blocked", "email": "blocked@example.com"},
        )

    assert exc_info.value.code == IDENTITY_RESOLUTION_FAILED
    assert exc_info.value.http_status == 403


@pytest.mark.django_db(transaction=True)
def test_complete_link_populates_credential_token_fields(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential link persists token-derived expiry, scopes, and refresh telemetry."""

    link_user = get_user_model().objects.create_user(username="token-fields", email="tokens@example.com")
    oauth_client = _oauth_client()
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(link_user.pk),
        flow="link",
    )
    tokens = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "id_token": "id-token",
        "expires_in": "120",
        "scope": "openid email profile",
    }
    monkeypatch.setattr(OAuthClientOidcProtocol, "exchange_code", lambda self, **kwargs: tokens)
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "verify_id_token",
        lambda self, id_token, **kwargs: {"sub": "sub-token-fields", "email": "tokens@example.com"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token, params=None: {})

    before = timezone.now()
    result = identity.complete_link(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/callback",
    )
    account = result.account

    with system_context(reason="test oidc assertions"):
        credential = Credential.objects.get(user=link_user, oauth_client=oauth_client, external_account=account)
    account.refresh_from_db()
    assert account.oauth_client_id == oauth_client.pk
    assert account.credential_id == credential.pk
    assert account.credential_status == "active"
    assert credential.expires_at is not None
    assert before + timedelta(seconds=119) <= credential.expires_at <= timezone.now() + timedelta(seconds=121)
    assert credential.granted_scopes == ["openid", "email", "profile"]
    assert credential.last_refresh_at is not None
    assert credential.last_refresh_status == "ok"
    assert credential.reveal()["refresh_token"] == "refresh-token"


@pytest.mark.django_db(transaction=True)
def test_complete_account_connect_links_oauth_userinfo_claims_and_credential(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain OAuth connect flow resolves identity from configured userinfo claims."""

    link_user = get_user_model().objects.create_user(username="oauth-connect", email="connect@example.com")
    oauth_client = _oauth_client(
        slug="plain-oauth",
        oidc=False,
        external_id_claim="id",
        display_name_claim="name",
        avatar_url_claim="avatar_url",
    )
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/oauth/callback",
        user_id=str(link_user.pk),
        flow=oauth_state.StateFlow.CONNECT,
    )
    tokens = {
        "access_token": "oauth-access",
        "refresh_token": "oauth-refresh",
        "scope": "offline email",
    }
    claims = {
        "id": "acct_123",
        "email": "connect@example.com",
        "name": "Connected User",
        "avatar_url": "https://avatar.example/u.png",
    }

    def exchange_code(self: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["state"] == state_token
        return tokens

    monkeypatch.setattr(OAuthClientProtocol, "exchange_code", exchange_code)
    monkeypatch.setattr(OAuthClientProtocol, "fetch_userinfo", lambda self, access_token, params=None: claims)
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "verify_id_token",
        lambda self, id_token, **kwargs: pytest.fail("plain OAuth connect must not verify an ID token"),
    )

    result = complete_account_connect(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/oauth/callback",
    )

    account = result.account
    credential = result.credential
    account.refresh_from_db()
    credential.refresh_from_db()
    assert result.user.pk == link_user.pk
    assert result.next_path == "/"
    assert account.external_id == "acct_123"
    assert account.email == "connect@example.com"
    assert account.display_name == "Connected User"
    assert account.avatar_url == "https://avatar.example/u.png"
    assert account.credential_id == credential.pk
    assert credential.user_id == link_user.pk
    assert credential.external_account_id == account.pk
    assert credential.reveal()["access_token"] == "oauth-access"


@pytest.mark.django_db(transaction=True)
def test_complete_account_connect_falls_back_to_token_response_account(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restricted tokens can resolve an account without userinfo scope."""

    link_user = get_user_model().objects.create_user(
        username="oauth-token-account",
        email="local@example.com",
    )
    oauth_client = _oauth_client(
        slug="token-account",
        oidc=False,
        external_id_claim="account.uuid",
        email_claim="account.email_address",
        display_name_claim="account.display_name",
    )
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/oauth/callback",
        user_id=str(link_user.pk),
        flow=oauth_state.StateFlow.CONNECT,
    )

    def exchange_code(self: OAuthClientProtocol, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        self._token_response_claims = {
            "account": {
                "uuid": "anthropic-account-uuid",
                "email_address": "subscriber@example.com",
            },
        }
        return {
            "access_token": "long-lived-access",
            "expires_in": 31536000,
            "scope": "user:inference",
        }

    monkeypatch.setattr(OAuthClientProtocol, "exchange_code", exchange_code)
    monkeypatch.setattr(OAuthClientProtocol, "fetch_userinfo", lambda self, access_token, params=None: {})

    result = complete_account_connect(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/oauth/callback",
    )

    result.account.refresh_from_db()
    assert result.account.external_id == "anthropic-account-uuid"
    assert result.account.email == "subscriber@example.com"
    assert result.account.display_name == "subscriber@example.com"
    assert result.claims == {
        "account": {
            "uuid": "anthropic-account-uuid",
            "email_address": "subscriber@example.com",
        },
    }
    assert result.credential.reveal() == {
        "access_token": "long-lived-access",
        "expires_in": 31536000,
        "scope": "user:inference",
    }


@pytest.mark.django_db(transaction=True)
def test_complete_account_connect_rejects_missing_stable_external_id(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Providers must return the configured stable account id claim."""

    link_user = get_user_model().objects.create_user(username="oauth-missing-id", email="missing@example.com")
    oauth_client = _oauth_client(slug="missing-id", oidc=False, external_id_claim="id")
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/oauth/callback",
        user_id=str(link_user.pk),
        flow=oauth_state.StateFlow.CONNECT,
    )
    monkeypatch.setattr(
        OAuthClientProtocol,
        "exchange_code",
        lambda self, **kwargs: {"access_token": "oauth-access"},
    )
    monkeypatch.setattr(
        OAuthClientProtocol,
        "fetch_userinfo",
        lambda self, access_token, params=None: {"email": "missing@example.com"},
    )

    with pytest.raises(OAuthFlowError) as exc_info:
        complete_account_connect(
            oauth_client,
            code="code",
            state_token=state_token,
            redirect_uri="https://app.example/oauth/callback",
        )

    assert exc_info.value.code == EXTERNAL_ACCOUNT_RESOLUTION_FAILED
    assert exc_info.value.http_status == 400


@pytest.mark.django_db(transaction=True)
def test_credential_upsert_reasserts_active_status(
    composed_tables: None,
) -> None:
    """Re-upserting an OAuth credential reactivates a previously revoked row."""

    user = get_user_model().objects.create_user(username="reactivated", email="reactivated@example.com")
    oauth_client = _oauth_client()
    credential = Credential.objects.upsert_for_user(user, oauth_client, "oauth", {"access_token": "first-token"})
    with system_context(reason="test oidc setup"):
        Credential.objects.filter(pk=credential.pk).update(status=CredentialStatus.REVOKED)

    updated = Credential.objects.upsert_for_user(user, oauth_client, "oauth", {"access_token": "second-token"})

    updated.refresh_from_db()
    assert updated.status == CredentialStatus.ACTIVE
    assert updated.reveal()["access_token"] == "second-token"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("deferred", (False, True), ids=("loaded", "deferred"))
def test_credential_disconnect_guard_blocks_last_oidc_sign_in(
    composed_tables: None,
    deferred: bool,
) -> None:
    """The sign-in invariant is enforced with loaded or deferred credential fields."""

    user = get_user_model().objects.create_user(username="oidc-only", email="oidc-only@example.com")
    oauth_client = _oauth_client()
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-oidc-only",
        owner=user,
        email="oidc-only@example.com",
        identity_claims={"sub": "sub-oidc-only"},
    )
    credential = Credential.objects.upsert_for_user(
        user,
        oauth_client,
        "oauth",
        {"access_token": "access"},
        external_account=account,
    )

    if deferred:
        credential = Credential._base_manager.only("pk").get(pk=credential.pk)

    with pytest.raises(ValidationError) as exc_info:
        Credential.objects.prepare_disconnect(credential)

    assert exc_info.value.code == "only_sign_in_method"
    assert exc_info.value.messages == ["This is your only sign-in method."]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("other_sign_in", ["password", "oidc", "oauth_only", "local"])
def test_credential_disconnect_preserves_other_sign_in_methods(composed_tables: None, other_sign_in: str) -> None:
    """The model contribution permits alternatives and non-login credentials."""

    user = get_user_model().objects.create_user(
        username="disconnect-permitted",
        password="a-password" if other_sign_in == "password" else None,
    )
    if other_sign_in == "local":
        credential = Credential.objects.create_local_credential(
            user,
            kind="static_token",
            name="API",
            material={"api_key": "token"},
        )
    else:
        client = _oauth_client(oidc=other_sign_in != "oauth_only")
        account = ExternalAccount.objects.link(client, "subject", owner=user)
        credential = Credential.objects.upsert_for_user(
            user,
            client,
            "oauth",
            {"access_token": "token"},
            external_account=account,
        )
        if other_sign_in == "oidc":
            other_client = _oauth_client(slug="other")
            other_account = ExternalAccount.objects.link(other_client, "other-subject", owner=user)
            Credential.objects.upsert_for_user(
                user,
                other_client,
                "oauth",
                {"access_token": "other-token"},
                external_account=other_account,
            )

    credential.check_disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changed_field", ["password", "login_enabled"])
@pytest.mark.parametrize("previous", [False, True])
def test_credential_disconnect_reads_current_sign_in_facts_despite_cached_relations(
    composed_tables: None, changed_field: str, previous: bool
) -> None:
    """Password and provider-policy changes supersede the credential's cached rows."""

    del composed_tables
    user = get_user_model().objects.create_user(
        username="disconnect-stale-cache",
        password="old-password" if changed_field == "password" and previous else None,
    )
    client = _oauth_client(oidc=previous if changed_field == "login_enabled" else True)
    account = ExternalAccount.objects.link(client, "stale-cache-subject", owner=user)
    credential = Credential.objects.upsert_for_user(
        user, client, "oauth", {"access_token": "token"}, external_account=account
    )
    with system_context(reason="test oidc cached sign-in facts"):
        cached = Credential._base_manager.select_related("user", "oauth_client").get(pk=credential.pk)
        if changed_field == "password":
            assert cached.user.has_usable_password() is previous
            user.set_password("new-password" if not previous else None)
            user.save(update_fields=["password"])
            permitted = not previous
        else:
            assert cached.oauth_client.login_enabled is previous
            client.login_enabled = not previous
            client.save(update_fields=["login_enabled"])
            permitted = previous

        if permitted:
            cached.check_disconnect()
        else:
            with pytest.raises(ValidationError) as exc_info:
                cached.check_disconnect()
            assert exc_info.value.code == "only_sign_in_method"
            assert exc_info.value.messages == ["This is your only sign-in method."]


@pytest.mark.django_db(transaction=True)
def test_low_level_credential_delete_does_not_run_disconnect_guard(
    composed_tables: None,
) -> None:
    """The OIDC guard belongs to explicit disconnect, not model-delete signals."""

    user = get_user_model().objects.create_user(username="oidc-cascade", email="oidc-cascade@example.com")
    oauth_client = _oauth_client(slug="oidc-cascade")
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-oidc-cascade",
        owner=user,
        email="oidc-cascade@example.com",
        identity_claims={"sub": "sub-oidc-cascade"},
    )
    credential = Credential.objects.upsert_for_user(
        user,
        oauth_client,
        "oauth",
        {"access_token": "access"},
        external_account=account,
    )

    with system_context(reason="test oidc raw credential delete"):
        credential.delete()

    with system_context(reason="test oidc cascade assertion"):
        assert not Credential.objects.filter(pk=credential.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_userinfo_claims_merge_into_login_and_link_claims(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Userinfo claims enrich ID-token claims before login resolve and account link."""

    link_user = get_user_model().objects.create_user(username="merged-claims", email="merged@example.com")
    oauth_client = _oauth_client()
    token_response = {"access_token": "access", "id_token": "id-token"}
    id_claims = {
        "sub": "sub-merged",
        "email": "id-token@example.com",
        "email_verified": True,
        "preferred_username": "id-token-name",
    }
    userinfo_claims = {
        "sub": "sub-merged",
        "email": "userinfo@example.com",
        "preferred_username": "userinfo-name",
        "groups": ["ops"],
    }
    expected_claims = {
        "sub": "sub-merged",
        "email": "id-token@example.com",
        "email_verified": True,
        "preferred_username": "id-token-name",
        "groups": ["ops"],
    }
    captured: dict[str, Any] = {}

    def resolve_user(
        oauth_client_arg: Any,
        *,
        sub: str,
        email: str | None,
        claims: dict[str, Any],
    ) -> Any:
        captured["oauth_client"] = oauth_client_arg
        captured["sub"] = sub
        captured["email"] = email
        captured["claims"] = claims
        return link_user

    monkeypatch.setattr(OAuthClientOidcProtocol, "exchange_code", lambda self, **kwargs: token_response)
    monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", lambda self, id_token, **kwargs: id_claims)
    userinfo_calls = []

    def fetch_userinfo(self, access_token, *, params=None):
        userinfo_calls.append((access_token, params))
        return userinfo_claims

    monkeypatch.setattr(type(oauth_client), "userinfo_params", lambda self, protocol, token: {"proof": token})
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", fetch_userinfo)
    monkeypatch.setattr(identity, "resolve", resolve_user)

    login_state, _login_record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        next_path="/login-next",
    )
    login_result = identity.complete_login(
        oauth_client,
        code="code",
        state_token=login_state,
        redirect_uri="https://app.example/callback",
    )

    assert login_result.user.pk == link_user.pk
    assert login_result.user.backend == identity.SESSION_AUTH_BACKEND
    assert login_result.claims == expected_claims
    assert login_result.next_path == "/login-next"
    assert captured["oauth_client"] == oauth_client
    assert captured["sub"] == "sub-merged"
    assert captured["email"] == "id-token@example.com"
    assert captured["claims"] == expected_claims

    # First login establishes the user's own party: a controlled handle + confirmed self-link.
    with system_context(reason="test verify login claim"):
        own_handle = PartiesHandle.objects.get(value="id-token@example.com")
        person = PartiesPerson.objects.for_user(link_user)
        assert own_handle.controller_id == link_user.pk
        assert own_handle.party_id == person.pk

    link_state, _link_record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(link_user.pk),
        next_path="/link-next",
        flow="link",
    )
    link_result = identity.complete_link(
        oauth_client,
        code="code",
        state_token=link_state,
        redirect_uri="https://app.example/callback",
    )
    account = link_result.account

    account.refresh_from_db()
    assert link_result.user.pk == link_user.pk
    assert link_result.claims == expected_claims
    assert link_result.next_path == "/link-next"
    assert account.identity_claims == expected_claims
    assert userinfo_calls == [("access", {"proof": "access"}), ("access", {"proof": "access"})]


@pytest.mark.django_db(transaction=True)
def test_complete_login_does_not_claim_an_unverified_email(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OIDC email becomes a parties identity claim only when explicitly verified."""

    del composed_tables
    user = get_user_model().objects.create_user(username="unverified-login", email="unverified@example.com")
    oauth_client = _oauth_client(slug="unverified-claim")
    state_token, _record = oauth_state.issue(oauth_client, "https://app.example/callback")
    claims = {
        "sub": "sub-unverified",
        "email": "unverified@example.com",
        "email_verified": False,
    }
    claimed: list[str] = []
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "exchange_code",
        lambda self, **kwargs: {"access_token": "access", "id_token": "id-token"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", lambda self, id_token, **kwargs: claims)
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token, params=None: {})
    monkeypatch.setattr(identity, "resolve", lambda *args, **kwargs: user)
    monkeypatch.setattr(identity, "_claim_login_handle", lambda user_arg, email, claims_arg: claimed.append(email))

    completion = identity.complete_login(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/callback",
    )

    assert completion.user.pk == user.pk
    assert claimed == []


@pytest.mark.django_db(transaction=True)
def test_complete_login_contains_parties_bookkeeping_failure(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A parties bookkeeping exception is logged but never turns a verified login into failure."""

    del composed_tables
    user = get_user_model().objects.create_user(username="bookkeeping-login", email="bookkeeping@example.com")
    oauth_client = _oauth_client(slug="bookkeeping-failure")
    state_token, _record = oauth_state.issue(oauth_client, "https://app.example/callback")
    claims = {
        "sub": "sub-bookkeeping",
        "email": "bookkeeping@example.com",
        "email_verified": True,
    }
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "exchange_code",
        lambda self, **kwargs: {"access_token": "access", "id_token": "id-token"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", lambda self, id_token, **kwargs: claims)
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token, params=None: {})
    monkeypatch.setattr(identity, "resolve", lambda *args, **kwargs: user)

    def fail_bookkeeping(user_arg: Any, email: str, claims_arg: dict[str, Any]) -> None:
        del user_arg, email, claims_arg
        raise RuntimeError("parties unavailable")

    monkeypatch.setattr(identity, "_claim_login_handle", fail_bookkeeping)

    completion = identity.complete_login(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/callback",
    )

    assert completion.user.pk == user.pk
    assert "Could not record the verified OIDC email in parties" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_complete_link_rejects_account_owned_by_another_user(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account linking fails when the external account belongs to another user."""

    owner = get_user_model().objects.create_user(username="linked-owner", email="owner@example.com")
    other = get_user_model().objects.create_user(username="linked-other", email="other@example.com")
    oauth_client = _oauth_client()
    ExternalAccount.objects.link(
        oauth_client,
        "sub-linked",
        owner=owner,
        email="owner@example.com",
        identity_claims={"sub": "sub-linked"},
    )
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(other.pk),
        flow="link",
    )
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "exchange_code",
        lambda self, **kwargs: {"access_token": "access", "id_token": "id-token"},
    )
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "verify_id_token",
        lambda self, id_token, **kwargs: {"sub": "sub-linked", "email": "other@example.com"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token, params=None: {})

    with pytest.raises(OAuthFlowError) as exc_info:
        identity.complete_link(
            oauth_client,
            code="code",
            state_token=state_token,
            redirect_uri="https://app.example/callback",
        )

    assert exc_info.value.code == "account_already_linked"
    assert exc_info.value.http_status == 409


@pytest.mark.django_db(transaction=True)
def test_complete_link_binds_to_state_user_after_session_swap(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account linking uses the start-flow user captured in state, not a later session user."""

    start_user = get_user_model().objects.create_user(username="link-start", email="start@example.com")
    swapped_user = get_user_model().objects.create_user(username="link-swapped", email="swapped@example.com")
    oauth_client = _oauth_client()
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(start_user.pk),
        flow="link",
    )
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "exchange_code",
        lambda self, **kwargs: {"access_token": "access", "id_token": "id-token"},
    )
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "verify_id_token",
        lambda self, id_token, **kwargs: {"sub": "sub-swapped", "email": "start@example.com"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token, params=None: {})

    result = identity.complete_link(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/callback",
    )
    account = result.account

    with system_context(reason="test oidc assertions"):
        owner = ExternalAccount.objects.get(pk=account.pk).owner
        start_credential = Credential.objects.filter(user=start_user, external_account=account).exists()
        swapped_credential = Credential.objects.filter(user=swapped_user, external_account=account).exists()
    assert owner is not None
    assert owner.pk == start_user.pk
    assert start_credential is True
    assert swapped_credential is False


def test_state_records_are_single_use() -> None:
    """Consumed state records cannot be consumed again."""

    oauth_client = SimpleNamespace(sqid="clt_test", pk=1, supports_pkce=False)
    state_token, record = oauth_state.issue(oauth_client, "https://app.example/callback")

    assert record.nonce != state_token
    assert oauth_state.consume(state_token) == record
    with pytest.raises(OAuthFlowError) as exc_info:
        oauth_state.consume(state_token)

    assert exc_info.value.code == INVALID_STATE
    assert exc_info.value.http_status == 400


def test_state_consume_rejects_failed_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """A state replay loses the delete race and is rejected."""

    record = oauth_state.StateRecord(
        oauth_client_id="clt_race",
        redirect_uri="https://app.example/callback",
        user_id=None,
        nonce="nonce",
        code_verifier=None,
        created_at=timezone.now(),
    )

    class ReplayCache:
        def get(self, key: str) -> oauth_state.StateRecord:
            del key
            return record

        def delete(self, key: str) -> bool:
            del key
            return False

    monkeypatch.setattr(oauth_state, "cache", ReplayCache())

    with pytest.raises(OAuthFlowError) as exc_info:
        oauth_state.consume("state-token")

    assert exc_info.value.code == INVALID_STATE


@override_settings(DEBUG=False, ANGEE_INTEGRATE_ALLOW_LOCAL_OAUTH_STATE_CACHE=False)
def test_state_issue_rejects_locmem_cache_without_opt_in() -> None:
    """Production OAuth state needs a shared cache, not per-process LocMemCache."""

    oauth_client = SimpleNamespace(sqid="clt_locmem", pk=1, supports_pkce=False)

    with pytest.raises(ImproperlyConfigured, match="shared cache"):
        oauth_state.issue(oauth_client, "https://app.example/callback")


def test_state_flow_binding_rejects_cross_flow_completion() -> None:
    """A login token cannot complete a link, and a link token cannot complete a login."""

    oauth_client = SimpleNamespace(sqid="clt_flow", pk=7, supports_pkce=False, login_enabled=True)

    login_token, _login = oauth_state.issue(oauth_client, "https://app.example/callback", flow="login")
    with pytest.raises(OAuthFlowError) as link_exc:
        identity.complete_link(
            oauth_client,
            code="code",
            state_token=login_token,
            redirect_uri="https://app.example/callback",
        )
    assert link_exc.value.code == INVALID_STATE

    link_token, _link = oauth_state.issue(oauth_client, "https://app.example/callback", user_id="1", flow="link")
    with pytest.raises(OAuthFlowError) as login_exc:
        identity.complete_login(
            oauth_client,
            code="code",
            state_token=link_token,
            redirect_uri="https://app.example/callback",
        )
    assert login_exc.value.code == INVALID_STATE


def _oauth_client(slug: str = "oidc", *, oidc: bool = True, **overrides: Any) -> OAuthClient:
    """Create one enabled OAuth client, setting OIDC login fields on the same row."""

    defaults: dict[str, Any] = {
        "display_name": "OIDC test",
        "client_id": "oidc-client",
        "client_secret": "secret",
        "discovery_url": "",
        "authorize_endpoint": "https://issuer.example/oauth/authorize",
        "token_endpoint": "https://issuer.example/oauth/token",
        "userinfo_endpoint": "https://issuer.example/oauth/userinfo",
        "is_enabled": True,
        "supports_pkce": True,
        "issuer": "https://issuer.example" if oidc else "",
        "jwks_uri": "https://issuer.example/oauth/jwks" if oidc else "",
        "login_enabled": oidc,
        "link_on_email_match": False,
        "create_on_login": False,
        "allowed_email_domains": [],
    }
    defaults.update(overrides)
    with system_context(reason="test oidc setup"):
        client = OAuthClient.objects.create(slug=slug, **defaults)
    return client


def _stub_oauth_client(**overrides: Any) -> OAuthClient:
    """Return an OAuth-client-like object for OAuth-protocol tests."""

    authorize_param_values = overrides.pop("authorize_param_values", None)
    token_param_values = overrides.pop("token_param_values", None)
    defaults: dict[str, Any] = {
        "slug": "stub",
        "display_name": "Stub",
        "client_id": "oidc-client",
        "client_secret": "secret",
        "discovery_url": "",
        "authorize_endpoint": "https://issuer.example/oauth/authorize",
        "token_endpoint": "https://issuer.example/oauth/token",
        "revoke_endpoint": "",
        "userinfo_endpoint": "https://issuer.example/oauth/userinfo",
        "supports_pkce": False,
        "token_request_format": "form",
        "authorize_params": {},
        "token_params": {},
    }
    if authorize_param_values is not None:
        defaults["authorize_params"] = authorize_param_values
    if token_param_values is not None:
        defaults["token_params"] = token_param_values
    defaults.update(overrides)
    return OAuthClient(**defaults)


def _stub_login_oauth_client(*, oauth_client: OAuthClient | None = None, **overrides: Any) -> OAuthClient:
    """Return an OAuth-client-like object carrying OAuth and OIDC login fields."""

    client = oauth_client if oauth_client is not None else _stub_oauth_client()
    defaults: dict[str, Any] = {
        "issuer": "https://issuer.example",
        "jwks_uri": "https://issuer.example/oauth/jwks",
        "discovery_url": "",
        "login_enabled": True,
        "link_on_email_match": False,
        "create_on_login": False,
        "allowed_email_domains": [],
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(client, key, value)
    client.oauth_client = client
    client.fill_extension_fields_from_discovery = MethodType(
        OAuthClientOidc.fill_extension_fields_from_discovery, client
    )
    return client


class _FakeJwksClient:
    """JWKS client test double returning a stable signing key."""

    def __init__(self, key: object = "secret") -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        """Return the key object shape PyJWT exposes."""

        del token
        return SimpleNamespace(key=self.key)


@pytest.mark.django_db
def test_oidc_group_by_login_enabled() -> None:
    """OAuth clients group directly by the login-enabled OIDC discriminator.

    Exercises the one-model shape: OIDC login is represented directly by
    ``OAuthClient.login_enabled``.
    """

    call_command("rebac", "sync", verbosity=0)
    with system_context(reason="test oidc group-by"):
        enabled = OAuthClient.objects.create(
            slug="enabled-client",
            display_name="Enabled",
            client_id="c1",
            is_enabled=True,
        )
        OAuthClient.objects.create(
            slug="disabled-client",
            display_name="Disabled",
            client_id="c2",
            is_enabled=False,
            login_enabled=False,
        )
        enabled.login_enabled = True
        enabled.save(update_fields=["login_enabled"])
        rows = compute_aggregation(
            OAuthClient.objects.all(),
            group_by=[("login_enabled", None)],
            aggregates=[(AggregateOp.COUNT, None)],
        )
    by_enabled = {row["login_enabled"]: row["count"] for row in rows}
    assert by_enabled == {True: 1, False: 1}


@pytest.mark.django_db(transaction=True)
def test_oidc_login_picker_queryset_scope(composed_tables: None) -> None:
    """The OIDC extension owns the public login-picker queryset predicate."""

    del composed_tables
    _oauth_client(slug="picker-enabled", oidc=True, is_enabled=True)
    _oauth_client(slug="picker-disabled", oidc=True, is_enabled=False)
    _oauth_client(slug="picker-oauth", oidc=False, is_enabled=True)
    _oauth_client(slug="picker-no-client", oidc=True, is_enabled=True, client_id="")
    _oauth_client(
        slug="picker-discovery",
        oidc=True,
        is_enabled=True,
        authorize_endpoint="",
        token_endpoint="",
        discovery_url="https://issuer.example/.well-known/openid-configuration",
    )

    with system_context(reason="test oidc picker"):
        queryset = OAuthClient.login_picker_queryset(OAuthClient.objects.all())
        slugs = set(queryset.values_list("slug", flat=True))

    assert slugs == {"picker-enabled", "picker-discovery"}



@pytest.mark.usefixtures("composed_tables")
def test_oidc_link_refines_the_grant_before_userinfo_and_credential_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={
        "generic_oauth2": "tests.integrate_fixtures.RefiningProvider",
    }), system_context(reason="test refined OIDC link"):
        user = get_user_model().objects.create_user(username="refined-link")
        client = _oauth_client("refined-link-client")
        state_token, _record = oauth_state.issue(
            client, "https://app.example/callback", user_id=str(user.pk), flow="link",
        )
        grants = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/oauth/userinfo":
                assert request.headers["Authorization"] == "Bearer refined"
                assert request.url.params["proof"] == "refined"
                return httpx2.Response(200, json={"sub": "linked-subject", "name": "Linked identity"})
            body = parse.parse_qs(request.content.decode())
            grants.append(body["grant_type"][0])
            if body["grant_type"] == ["resource_grant"]:
                assert body["short_token"] == ["short"]
                return httpx2.Response(200, json={"access_token": "refined", "id_token": "refined-identity"})
            return httpx2.Response(200, json={"access_token": "short", "id_token": "initial-identity"})

        original = OAuthClientProtocol.__init__

        def with_transport(self: Any, oauth_client: Any) -> None:
            original(self, oauth_client)
            self._transport = httpx2.MockTransport(respond)

        def verify(self: Any, token: str, **kwargs: Any) -> dict[str, Any]:
            assert token == "initial-identity"
            return {"sub": "linked-subject"}

        monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
        monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", verify)
        result = identity.complete_link(
            client, code="code", state_token=state_token, redirect_uri="https://app.example/callback",
        )
        assert grants == ["authorization_code", "resource_grant"]
        assert result.account.external_id == "linked-subject"
        assert Credential.objects.get(user=user, oauth_client=client).secret_value() == "refined"


@pytest.mark.usefixtures("composed_tables")
def test_google_preset_owns_the_subject_bearing_profile_endpoint() -> None:
    with system_context(reason="test Google preset identity"):
        client = OAuthClient.objects.create(slug="google-profile", provider_type="google")
    endpoint = GoogleType.effective_defaults()["userinfo_endpoint"]
    assert endpoint == "https://openidconnect.googleapis.com/v1/userinfo"
    assert client.userinfo_endpoint == endpoint

    def profile(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == endpoint
        return httpx2.Response(200, json={"sub": "provider-subject"})

    protocol = OAuthClientProtocol(client)
    protocol._transport = httpx2.MockTransport(profile)
    claims = protocol.fetch_userinfo("fake")
    assert client.external_id_from_claims(claims) == "provider-subject"


def test_google_seed_discovers_the_preset_profile_without_a_conflicting_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_file = Path(__file__).parents[1] / "addons/angee/integrate/resources/install/010_integrate.oauthclient.yaml"
    seed = next(row for row in yaml.safe_load(seed_file.read_text()) if row["xref"] == "oauth_gemini")
    client = _stub_oauth_client(**{key: value for key, value in seed.items() if key != "xref"})
    endpoint = GoogleType.effective_defaults()["userinfo_endpoint"]
    discovery_calls: list[str] = []

    def discover(url: str, **kwargs: Any) -> dict[str, str]:
        discovery_calls.append(url)
        return {"userinfo_endpoint": endpoint}

    def profile(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == endpoint
        return httpx2.Response(200, json={"sub": "seed-subject"})

    monkeypatch.setattr(oauth_discovery, "_get_json", discover)
    monkeypatch.setattr(oauth_discovery.cache, "get", lambda key: None)
    protocol = OAuthClientProtocol(client)
    protocol._transport = httpx2.MockTransport(profile)
    claims = protocol.fetch_userinfo("fake")
    assert discovery_calls == [client.discovery_url]
    assert client.external_id_from_claims(claims) == "seed-subject"


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("flow_name", ["login", "link", "connect"])
@pytest.mark.parametrize("request_format", ["form", "json"])
def test_oauth_completion_returns_typed_outage_for_refused_endpoint(
    monkeypatch: pytest.MonkeyPatch, flow_name: str, request_format: str,
) -> None:
    schema = GraphQLSchemas([
        SchemaAddon(module.schemas) for module in (iam_schema, integrate_schema, oidc_schema)
    ]).build("console")
    with system_context(reason="test refused provider"):
        user = get_user_model().objects.create_user(username="refused-provider-user")
        client = _oauth_client("refused-provider", token_request_format=request_format)
    redirect = "https://app.example/callback"
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        client.token_endpoint = f"http://127.0.0.1:{reserved.getsockname()[1]}/token"
        token, _record = oauth_state.issue(
            client, redirect, user_id=str(user.pk) if flow_name != "login" else None, flow=flow_name,
        )
        monkeypatch.setattr(oauth_protocol, "HTTP_TIMEOUT_SECONDS", 1)
        monkeypatch.setattr(integrate_schema.flow, "remembered_oauth_client", lambda *args: client)
        mutation = {
            "login": "login_complete", "link": "link_account_complete", "connect": "connect_account_complete",
        }[flow_name]
        request = RequestFactory().post("/graphql/console/")
        request.user = AnonymousUser() if flow_name == "login" else user
        result = result_data(execute_schema(schema, f'''mutation {{
          {mutation}(code: "code", state: "{token}", redirect_uri: "{redirect}") {{
            error error_code
          }}
        }}''', request=request))[mutation]
    expected_code = "provider_unavailable" if flow_name == "connect" else TOKEN_EXCHANGE_FAILED
    assert result == {"error": OAuthFlowError(expected_code, 503).public_message, "error_code": expected_code}
    with system_context(reason="test refused provider wrote no credential"):
        assert not Credential.objects.filter(user=user, oauth_client=client).exists()


@pytest.mark.usefixtures("composed_tables")
def test_oidc_login_verifies_code_exchange_when_refinement_omits_id_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={
        "generic_oauth2": "tests.integrate_fixtures.RefiningProvider",
    }), system_context(reason="test nonce proof survives refinement"):
        user = get_user_model().objects.create_user(username="refined-login")
        client = _oauth_client("refined-login-client")
        ExternalAccount.objects.link(client, "login-subject", owner=user)
        token, record = oauth_state.issue(client, "https://app.example/callback")
        grants = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/oauth/userinfo":
                assert request.headers["Authorization"] == "Bearer refined"
                return httpx2.Response(200, json={"sub": "login-subject"})
            body = parse.parse_qs(request.content.decode())
            grants.append(body["grant_type"][0])
            if body["grant_type"] == ["resource_grant"]:
                return httpx2.Response(200, json={"access_token": "refined"})
            return httpx2.Response(200, json={"access_token": "short", "id_token": "nonce-bound-proof"})

        original = OAuthClientProtocol.__init__

        def with_transport(self: Any, oauth_client: Any) -> None:
            original(self, oauth_client)
            self._transport = httpx2.MockTransport(respond)

        def verify(self: Any, id_token: str, *, nonce: str) -> dict[str, Any]:
            assert id_token == "nonce-bound-proof"
            assert nonce == record.nonce
            return {"sub": "login-subject"}

        monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
        monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", verify)
        completed = identity.complete_login(
            client, code="code", state_token=token, redirect_uri="https://app.example/callback",
        )
        assert completed.user.pk == user.pk
        assert grants == ["authorization_code", "resource_grant"]


@pytest.mark.parametrize("operation", ["revoke", "metadata"])
def test_oauth_helpers_map_refused_endpoint_to_transient_flow_error(operation: str) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{reserved.getsockname()[1]}/oauth"
        with pytest.raises(OAuthFlowError) as raised:
            if operation == "revoke":
                OAuthClientProtocol(_stub_oauth_client(revoke_endpoint=endpoint)).revoke_token("synthetic-token")
            else:
                oauth_protocol._get_json(endpoint, error_code=DISCOVERY_FAILED)
    assert raised.value.http_status == 503 and raised.value.transient
    assert raised.value.code == (TOKEN_EXCHANGE_FAILED if operation == "revoke" else DISCOVERY_FAILED)


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("request_format", ["form", "json"])
def test_blank_secret_public_client_refreshes_credential(monkeypatch: pytest.MonkeyPatch, request_format: str) -> None:
    with system_context(reason="test public refresh"):
        user = get_user_model().objects.create_user(username="public-refresh")
        client = _oauth_client("public-refresh", client_secret="", token_request_format=request_format)
        credential = Credential.objects.upsert_for_user(
            user, client, "oauth", {"access_token": "old-access", "refresh_token": "old-refresh", "expires_in": 1},
        )

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = (
            json.loads(request.content) if request_format == "json" else dict(parse.parse_qsl(request.content.decode()))
        )
        assert body["grant_type"] == "refresh_token"
        assert body["client_id"] == client.client_id
        assert body["refresh_token"] == "old-refresh"
        assert "client_secret" not in body and "authorization" not in request.headers
        return httpx2.Response(200, json={
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600,
        })

    original = OAuthClientProtocol.__init__

    def with_transport(self: Any, oauth_client: Any) -> None:
        original(self, oauth_client)
        self._transport = httpx2.MockTransport(respond)

    monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
    with system_context(reason="test public refresh operation"):
        credential.refresh_now()
        assert credential.reveal()["access_token"] == "new-access"
        assert credential.reveal()["refresh_token"] == "new-refresh"
        assert credential.last_refresh_status == "ok"
        assert not credential.is_reconnect_required


@pytest.mark.usefixtures("composed_tables")
def test_oidc_login_with_blank_secret_public_client(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = GraphQLSchemas([
        SchemaAddon(module.schemas) for module in (iam_schema, integrate_schema, oidc_schema)
    ]).build("console")
    with system_context(reason="test public OIDC login"):
        user = get_user_model().objects.create_user(username="public-login")
        client = _oauth_client("public-login", client_secret="")
        ExternalAccount.objects.link(client, "public-subject", owner=user)
    redirect = "https://app.example/callback"
    token, record = oauth_state.issue(client, redirect)

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oauth/userinfo":
            return httpx2.Response(200, json={"sub": "public-subject"})
        body = dict(parse.parse_qsl(request.content.decode()))
        assert body["client_id"] == client.client_id and body["code_verifier"] == record.code_verifier
        assert "client_secret" not in body and "authorization" not in request.headers
        return httpx2.Response(200, json={"access_token": "access", "id_token": "proof"})

    original = OAuthClientProtocol.__init__

    def with_transport(self: Any, oauth_client: Any) -> None:
        original(self, oauth_client)
        self._transport = httpx2.MockTransport(respond)

    def verify(self: Any, id_token: str, *, nonce: str) -> dict[str, Any]:
        assert id_token == "proof" and nonce == record.nonce
        return {"sub": "public-subject"}

    monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
    monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", verify)
    monkeypatch.setattr(oidc_schema.oauth_flow, "remembered_oauth_client", lambda *args: client)
    request = RequestFactory().post("/graphql/console/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    result = result_data(execute_schema(schema, f'''mutation {{
      login_complete(code: "code", state: "{token}", redirect_uri: "{redirect}") {{ ok error error_code }}
    }}''', request=request))["login_complete"]
    assert result == {"ok": True, "error": None, "error_code": None}
    assert request.user.pk == user.pk


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("flow_name", ["login", "link"])
@pytest.mark.parametrize("transient", [False, True])
def test_oidc_completion_returns_typed_integration_error(
    monkeypatch: pytest.MonkeyPatch, flow_name: str, transient: bool,
) -> None:
    schema = GraphQLSchemas([
        SchemaAddon(module.schemas) for module in (iam_schema, integrate_schema, oidc_schema)
    ]).build("console")
    with system_context(reason="test OIDC integration refusal"):
        user = get_user_model().objects.create_user(username="integration-refusal")
        client = _oauth_client("integration-refusal")

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise IntegrationError("Connection was refused.", transient=transient)

    monkeypatch.setattr(oidc_schema.oauth_flow, "remembered_oauth_client", lambda *args: client)
    monkeypatch.setattr(identity, f"complete_{flow_name}", refuse)
    mutation = "login_complete" if flow_name == "login" else "link_account_complete"
    request = RequestFactory().post("/graphql/console/")
    request.user = user if flow_name == "link" else AnonymousUser()
    result = result_data(execute_schema(schema, f'''mutation {{
      {mutation}(code: "code", state: "state", redirect_uri: "https://app.example/callback") {{ error error_code }}
    }}''', request=request))[mutation]
    assert result == {
        "error": (
            "The provider is temporarily unavailable. Try again later." if transient else "Connection was refused."
        ),
        "error_code": "provider_unavailable" if transient else "connection_refused",
    }


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("userinfo_sub", [None, "different-subject", "verified-subject"])
def test_oidc_userinfo_email_link_requires_matching_subject(
    monkeypatch: pytest.MonkeyPatch, userinfo_sub: str | None,
) -> None:
    user = get_user_model().objects.create_user(username="email-link-target", email="target@example.com")
    client = _oauth_client("subject-check", link_on_email_match=True)
    redirect = "https://app.example/callback"
    token, _record = oauth_state.issue(client, redirect)
    userinfo = {"email": "target@example.com", "email_verified": True}
    if userinfo_sub is not None:
        userinfo["sub"] = userinfo_sub
    monkeypatch.setattr(OAuthClientOidcProtocol, "exchange_code", lambda self, **kwargs: {
        "access_token": "access", "id_token": "proof",
    })
    monkeypatch.setattr(
        OAuthClientOidcProtocol, "verify_id_token", lambda self, *args, **kwargs: {"sub": "verified-subject"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, *args, **kwargs: userinfo)
    if userinfo_sub == "verified-subject":
        completion = identity.complete_login(client, code="code", state_token=token, redirect_uri=redirect)
        assert completion.user.pk == user.pk
        assert completion.claims["email"] == "target@example.com"
    else:
        with pytest.raises(IdentityFlowError):
            identity.complete_login(client, code="code", state_token=token, redirect_uri=redirect)
        with system_context(reason="test untrusted userinfo never linked"):
            assert not ExternalAccount.objects.filter(oauth_client=client).exists()
