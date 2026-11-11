"""Tests for the OAuth/OIDC protocol, connect flow, and OIDC login resolution.

The provider is ``OidcProvider`` behind integrate's ``mock_transport``: every
exchange, discovery, JWKS and userinfo request crosses the real protocol code and
every ID token is verified by PyJWT against the provider's published key.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib import parse

import httpx2
import jwt
import pytest
import yaml
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone
from rebac import system_context
from strawberry_django_aggregates import AggregateOp, compute_aggregation

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from angee.iam import schema as iam_schema
from angee.iam_integrate_oidc import identity
from angee.iam_integrate_oidc import schema as oidc_schema
from angee.iam_integrate_oidc.errors import IDENTITY_RESOLUTION_FAILED, IdentityFlowError
from angee.iam_integrate_oidc.protocol import OAuthClientOidcProtocol
from angee.iam_integrate_oidc.providers import GoogleType
from angee.iam_integrate_oidc.testing.provider import KEY_ID, OidcProvider, signing_key
from angee.integrate import schema as integrate_schema
from angee.integrate.connect import complete_account_connect
from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import HTTP_TIMEOUT_SECONDS
from angee.integrate.models import AccountStatus, CredentialStatus
from angee.integrate.net import AddressNotAllowedError
from angee.integrate.oauth import client as oauth_protocol
from angee.integrate.oauth import discovery as oauth_discovery
from angee.integrate.oauth import flow as oauth_flow
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
from angee.integrate.oauth.providers import GenericOAuth2
from angee.integrate.testing.http import mock_transport, request_fields
from angee.messaging.testing.models import Handle as PartiesHandle
from tests.conftest import Credential, ExternalAccount, OAuthClient, SchemaAddon
from tests.test_parties_graphql import Person as PartiesPerson

# OIDC first login uses the same concrete Person and handle models as parties tests.


def test_discovery_fallback_fills_blank_authorize_endpoint() -> None:
    """A blank authorization endpoint is loaded from discovery."""

    provider = OidcProvider(issuer="https://fallback.example")
    discovery_url = f"{provider.issuer}/.well-known/openid-configuration"
    oauth_client = _stub_oauth_client(authorize_endpoint="")
    oidc = _stub_login_oauth_client(oauth_client=oauth_client, discovery_url=discovery_url)

    with mock_transport(provider) as requests:
        url = OAuthClientOidcProtocol(oidc).authorize_url(
            state="state-token",
            nonce="nonce-token",
            redirect_uri="https://app.example/callback",
            scopes=("openid", "email"),
        )

    assert [str(request.url) for request in requests] == [discovery_url]
    assert oauth_client.authorize_endpoint == "https://fallback.example/oauth/authorize"
    assert url.startswith("https://fallback.example/oauth/authorize?")


def test_ensure_endpoints_caches_document_by_discovery_url() -> None:
    """Discovery cache hits avoid a second discovery document fetch."""

    provider = OidcProvider(issuer="https://cached.example")
    discovery_url = f"{provider.issuer}/.well-known/openid-configuration"
    oauth_client = _stub_oauth_client(authorize_endpoint="", token_endpoint="", userinfo_endpoint="")
    oidc = _stub_login_oauth_client(oauth_client=oauth_client, issuer="", jwks_uri="", discovery_url=discovery_url)
    protocol = OAuthClientOidcProtocol(oidc)

    with mock_transport(provider) as requests:
        first = protocol.ensure_endpoints()
        second = protocol.ensure_endpoints()

    assert first == second == provider.metadata
    assert [str(request.url) for request in requests] == [discovery_url]
    assert oauth_client.token_endpoint == "https://cached.example/oauth/token"


@pytest.mark.parametrize(("answer", "status"), [("list", 400), ("text", 400), ("unreachable", 503)])
def test_discovery_rejects_invalid_documents_without_caching(answer: str, status: int) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if answer == "unreachable":
            raise httpx2.ConnectError("unavailable", request=request)
        return httpx2.Response(200, json=[]) if answer == "list" else httpx2.Response(200, text="not json")

    url = f"https://invalid-{answer}.example/discovery"
    with mock_transport(respond) as requests:
        for _attempt in range(2):
            with pytest.raises(OAuthFlowError) as refused:
                oauth_discovery.discovery_document(url)
            assert (refused.value.code, refused.value.http_status) == (DISCOVERY_FAILED, status)

    assert len(requests) == 2
    assert {(request.headers["Accept"], request.headers["User-Agent"]) for request in requests} == {
        ("application/json", oauth_protocol.USER_AGENT),
    }


def test_outbound_requests_send_honest_user_agent() -> None:
    """Outbound requests send an honest, non-browser User-Agent.

    Not the HTTP client's default (Anthropic's edge 403s ``python-httpx``/``Python-urllib``)
    and not a spoofed browser/curl UA (Anthropic 429s those) — an honest client UA passes.
    The userinfo GET and the Authlib token POST both carry it.
    """

    protocol = OAuthClientProtocol(_stub_oauth_client())
    with mock_transport(OidcProvider(userinfo={"sub": "profile"})) as requests:
        assert protocol.fetch_userinfo("access-token") == {"sub": "profile"}
        protocol.exchange_code(code="abc", redirect_uri="https://app.example/callback")

    assert [request.headers.get("user-agent") for request in requests] == [oauth_protocol.USER_AGENT] * 2
    assert {request.headers["Accept"] for request in requests} == {"application/json"}
    assert requests[0].headers["Authorization"] == "Bearer access-token"
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

    provider = OidcProvider(token={
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "account": {"uuid": "account-uuid", "email_address": "person@example.com"},
        "ignored": "not-token-material",
    })
    oauth_client = _stub_oauth_client(
        supports_pkce=True,
        token_request_format="json",
        token_param_values={"audience": "https://api.example", "expires_in": 31536000},
    )
    protocol = OAuthClientProtocol(oauth_client)

    with mock_transport(provider) as requests:
        tokens = protocol.exchange_code(
            code="auth-code",
            redirect_uri="https://app.example/callback",
            code_verifier="verifier",
            state="state-token",
        )

    assert tokens == {"access_token": "access-token", "refresh_token": "refresh-token"}
    assert protocol.token_response_claims == {
        "account": {"uuid": "account-uuid", "email_address": "person@example.com"},
    }
    [request] = requests
    assert str(request.url) == "https://issuer.example/oauth/token"
    assert request.headers["user-agent"] == oauth_protocol.USER_AGENT
    assert json.loads(request.content) == {
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


@pytest.mark.parametrize("request_format", ["form", "json"])
def test_refresh_token_posts_refresh_grant(request_format: str) -> None:
    """`refresh_token` posts a ``refresh_token`` grant and returns the renewed material."""

    renewed = {"access_token": "new-access", "refresh_token": "rotated", "expires_in": 7200}
    oauth_client = _stub_oauth_client(
        token_request_format=request_format,
        token_param_values={"audience": "https://api.example"},
    )

    with mock_transport(OidcProvider(token=renewed)) as requests:
        tokens = OAuthClientProtocol(oauth_client).refresh_token(refresh_token="stored-refresh")

    assert tokens == renewed
    [request] = requests
    assert str(request.url) == "https://issuer.example/oauth/token"
    assert request_fields(request) == {
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

    class PublicClient:
        provider = GenericOAuth2
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

    protocol = OAuthClientProtocol(PublicClient())
    with mock_transport(OidcProvider(token={"access_token": "synthetic-access"})) as requests:
        if grant == "authorization_code":
            result = protocol.exchange_code(
                code="synthetic-code",
                redirect_uri="https://app.example/callback",
                code_verifier="synthetic-verifier",
                state="synthetic-state",
            )
        else:
            result = protocol.refresh_token(refresh_token="synthetic-refresh")

    [request] = requests
    fields = request_fields(request)
    assert result["access_token"] == "synthetic-access"
    assert fields["client_id"] == "public-client-id"
    assert fields.get("code_verifier") == ("synthetic-verifier" if grant == "authorization_code" else None)
    assert "client_secret" not in fields
    assert "authorization" not in request.headers


def test_confidential_json_client_keeps_unreadable_secret_fail_closed() -> None:
    """A confidential client still fails before exchange when its secret is unreadable."""

    class ConfidentialClient:
        provider = GenericOAuth2
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

    with (
        mock_transport(lambda request: httpx2.Response(403, text="<html>403 Forbidden</html>")),
        pytest.raises(OAuthFlowError) as exc_info,
    ):
        OAuthClientProtocol(_stub_oauth_client()).exchange_code(
            code="auth-code", redirect_uri="https://app.example/callback",
        )

    assert exc_info.value.code == TOKEN_EXCHANGE_FAILED


def test_token_transport_error_log_omits_request_url_and_client_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Token transport telemetry contains a class and slug, without request metadata."""

    canary_url = "https://issuer.example/oauth/token?code=canary-code"

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("failed " + canary_url, request=request)

    oauth_client = _stub_oauth_client(token_endpoint=canary_url)
    with mock_transport(refuse), pytest.raises(OAuthFlowError):
        OAuthClientProtocol(oauth_client).exchange_code(code="canary-code", redirect_uri="https://app.example/callback")

    assert "ConnectError" in caplog.text
    assert "canary-code" not in caplog.text
    assert oauth_client.client_id not in caplog.text


@pytest.mark.parametrize("request_format", ["form", "json"])
def test_exchange_code_public_client_posts_client_id_and_verifier(request_format: str) -> None:
    """A public client (no secret) still posts ``client_id`` and the PKCE ``code_verifier``
    (RFC 7636) and never a ``client_secret``."""

    protocol = OAuthClientProtocol(_stub_oauth_client(
        client_secret="", supports_pkce=True, token_request_format=request_format,
    ))
    with mock_transport(OidcProvider()) as requests:
        protocol.exchange_code(
            code="auth-code",
            redirect_uri="https://app.example/callback",
            code_verifier="verifier",
            state="state-token",
        )

    [request] = requests
    body = request_fields(request)
    assert "authorization" not in request.headers
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
    token_request_format: str,
) -> None:
    """Every OAuth call reaches a live provider through Authlib and the real SSRF pin.

    Regression: Authlib builds its OAuth2 client on httpx2, and a transport from another
    HTTP stack fails on its first request, so sign-in code exchange and refresh broke.
    Only the provider is a test double; the Authlib session, the JSON shim, the GET
    helper, ``PinnedTransport`` (``allow_private`` admits loopback, never cloud
    metadata) and the sockets are the production path.
    """

    base_url, received = loopback_provider
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
    metadata_endpoint = _stub_oauth_client(token_endpoint="http://169.254.169.254/oauth/token")
    with pytest.raises(AddressNotAllowedError):
        OAuthClientProtocol(metadata_endpoint).exchange_code(code="code", redirect_uri="https://app.example/callback")


@pytest.mark.parametrize("claim,value", [("iss", "https://wrong.example"), ("aud", "other-client"), ("nonce", "wrong")])
def test_verify_id_token_rejects_wrong_native_claim(claim: str, value: str) -> None:
    """Native PyJWT verification enforces issuer, audience and the flow's nonce for signed tokens."""

    provider = OidcProvider()
    token = provider.id_token(**{"sub": "native-claim-test", "nonce": "nonce", claim: value})
    with mock_transport(provider), pytest.raises(OAuthFlowError) as exc_info:
        OAuthClientOidcProtocol(_stub_login_oauth_client()).verify_id_token(token, nonce="nonce")
    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_accepts_rs256_and_rejects_ps256() -> None:
    """ID token verification accepts only the configured asymmetric algorithms."""

    provider = OidcProvider()
    protocol = OAuthClientOidcProtocol(_stub_login_oauth_client())
    now = int(time.time())
    claims = {"aud": provider.client_id, "iss": provider.issuer, "iat": now, "exp": now + 60, "nonce": "nonce"}
    ps256_token = jwt.encode({**claims, "sub": "sub-alg"}, signing_key(), algorithm="PS256", headers={"kid": KEY_ID})

    with mock_transport(provider):
        verified = protocol.verify_id_token(provider.id_token(sub="sub-alg", nonce="nonce"), nonce="nonce")
        assert verified["sub"] == "sub-alg"
        with pytest.raises(OAuthFlowError) as exc_info:
            protocol.verify_id_token(ps256_token, nonce="nonce")
    assert exc_info.value.code == INVALID_ID_TOKEN


def test_jwks_is_fetched_once_per_uri_through_the_oauth_get_policy() -> None:
    """Verification fetches a URI's JWKS once, with the OAuth GET headers; PyJWT caches the set."""

    provider = OidcProvider(issuer="https://jwks-once.example")
    oidc = _stub_login_oauth_client(issuer=provider.issuer, jwks_uri=provider.metadata["jwks_uri"])

    with mock_transport(provider) as requests:
        for subject in ("first", "second"):
            token = provider.id_token(sub=subject, nonce="nonce")
            assert OAuthClientOidcProtocol(oidc).verify_id_token(token, nonce="nonce")["sub"] == subject

    [request] = requests
    assert str(request.url) == provider.metadata["jwks_uri"]
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == oauth_protocol.USER_AGENT
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), HTTP_TIMEOUT_SECONDS)


@pytest.mark.parametrize("failure", ["transport", "http", "json"])
def test_jwks_fetch_failure_refuses_the_id_token(failure: str) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if failure == "transport":
            raise httpx2.ConnectError("unavailable", request=request)
        return httpx2.Response(503 if failure == "http" else 200, text="not json")

    provider = OidcProvider(issuer=f"https://jwks-{failure}.example")
    oidc = _stub_login_oauth_client(issuer=provider.issuer, jwks_uri=provider.metadata["jwks_uri"])
    with mock_transport(respond), pytest.raises(OAuthFlowError) as refused:
        OAuthClientOidcProtocol(oidc).verify_id_token(provider.id_token(sub="s", nonce="n"), nonce="n")
    assert refused.value.code == INVALID_ID_TOKEN


def test_resolver_existing_external_account_returns_owner(
    db: None,
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


def test_resolver_blocks_non_active_account(db: None) -> None:
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


def test_external_account_manager_coerces_status_member_names(db: None) -> None:
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


def test_resolver_blocks_inactive_user(db: None) -> None:
    """A deactivated owner must not authenticate via OIDC (parity with the password path)."""

    user = get_user_model().objects.create_user(username="inactive-owner", email="ina@example.com")
    user.is_active = False
    with system_context(reason="test.oidc.inactive_user"):
        user.save(update_fields=["is_active"])
    oauth_client = _oauth_client()
    ExternalAccount.objects.link(oauth_client, "sub-inactive", owner=user, email="ina@example.com")

    with pytest.raises(OAuthFlowError):
        identity.resolve(oauth_client, sub="sub-inactive", email="ina@example.com", claims={"sub": "sub-inactive"})


def test_resolver_blocks_existing_external_account_with_service_owner(db: None) -> None:
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


def test_resolver_link_on_email_match_requires_verified_email(
    db: None,
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


def test_resolver_create_on_login_requires_verified_email(
    db: None,
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


def test_resolver_link_on_email_match_creates_external_account(
    db: None,
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


def test_resolver_link_on_email_match_skips_service_accounts(
    db: None,
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


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_rejects_ambiguous_email() -> None:
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


def test_resolver_create_on_login_provisions_user_and_external_account(
    db: None,
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


def test_resolver_create_on_login_sets_user_names_from_claims(
    db: None,
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


def test_async_resolver_create_on_login_provisions_user_and_external_account(
    db: None,
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


def test_resolver_disallowed_domain_raises_403(
    db: None,
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


def test_complete_link_populates_credential_token_fields(db: None) -> None:
    """Credential link persists token-derived expiry, scopes, and refresh telemetry."""

    link_user = get_user_model().objects.create_user(username="token-fields", email="tokens@example.com")
    oauth_client = _oauth_client()
    state_token, record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(link_user.pk),
        flow="link",
    )
    provider = OidcProvider(
        token={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": "120",
            "scope": "openid email profile",
        },
        id_claims={"sub": "sub-token-fields", "email": "tokens@example.com", "nonce": record.nonce},
    )

    before = timezone.now()
    with mock_transport(provider):
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


def test_complete_account_connect_links_oauth_userinfo_claims_and_credential(db: None) -> None:
    """A plain OAuth connect flow resolves identity from configured userinfo claims, never an ID token."""

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
    provider = OidcProvider(
        token={
            "access_token": "oauth-access",
            "refresh_token": "oauth-refresh",
            "scope": "offline email",
            "id_token": "unverifiable",
        },
        userinfo={
            "id": "acct_123",
            "email": "connect@example.com",
            "name": "Connected User",
            "avatar_url": "https://avatar.example/u.png",
        },
    )

    with mock_transport(provider):
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


def test_complete_account_connect_falls_back_to_token_response_account(db: None) -> None:
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
    provider = OidcProvider(token={
        "access_token": "long-lived-access",
        "expires_in": 31536000,
        "scope": "user:inference",
        "account": {"uuid": "anthropic-account-uuid", "email_address": "subscriber@example.com"},
    })

    with mock_transport(provider):
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


def test_complete_account_connect_rejects_missing_stable_external_id(db: None) -> None:
    """Providers must return the configured stable account id claim."""

    link_user = get_user_model().objects.create_user(username="oauth-missing-id", email="missing@example.com")
    oauth_client = _oauth_client(slug="missing-id", oidc=False, external_id_claim="id")
    state_token, _record = oauth_state.issue(
        oauth_client,
        "https://app.example/oauth/callback",
        user_id=str(link_user.pk),
        flow=oauth_state.StateFlow.CONNECT,
    )
    provider = OidcProvider(token={"access_token": "oauth-access"}, userinfo={"email": "missing@example.com"})

    with mock_transport(provider), pytest.raises(OAuthFlowError) as exc_info:
        complete_account_connect(
            oauth_client,
            code="code",
            state_token=state_token,
            redirect_uri="https://app.example/oauth/callback",
        )

    assert exc_info.value.code == EXTERNAL_ACCOUNT_RESOLUTION_FAILED
    assert exc_info.value.http_status == 400


def test_credential_upsert_reasserts_active_status(
    db: None,
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


@pytest.mark.parametrize("deferred", (False, True), ids=("loaded", "deferred"))
def test_credential_disconnect_guard_blocks_last_oidc_sign_in(
    db: None,
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

    with transaction.atomic(), pytest.raises(ValidationError) as exc_info:
        Credential.objects.prepare_disconnect(credential)

    assert exc_info.value.code == "only_sign_in_method"
    assert exc_info.value.messages == ["This is your only sign-in method."]


@pytest.mark.parametrize("other_sign_in", ["password", "oidc", "oauth_only", "local"])
def test_credential_disconnect_preserves_other_sign_in_methods(db: None, other_sign_in: str) -> None:
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

    with transaction.atomic():
        credential.check_disconnect()


@pytest.mark.parametrize("changed_field", ["password", "login_enabled"])
@pytest.mark.parametrize("previous", [False, True])
def test_credential_disconnect_reads_current_sign_in_facts_despite_cached_relations(
    db: None, changed_field: str, previous: bool
) -> None:
    """Password and provider-policy changes supersede the credential's cached rows."""

    del db
    user = get_user_model().objects.create_user(
        username="disconnect-stale-cache",
        password="old-password" if changed_field == "password" and previous else None,
    )
    client = _oauth_client(oidc=previous if changed_field == "login_enabled" else True)
    account = ExternalAccount.objects.link(client, "stale-cache-subject", owner=user)
    credential = Credential.objects.upsert_for_user(
        user, client, "oauth", {"access_token": "token"}, external_account=account
    )
    with system_context(reason="test oidc cached sign-in facts"), transaction.atomic():
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


def test_low_level_credential_delete_does_not_run_disconnect_guard(
    db: None,
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


def test_userinfo_claims_merge_into_link_and_login_claims(db: None) -> None:
    """Userinfo claims enrich, and never override, verified ID-token claims on link and login."""

    link_user = get_user_model().objects.create_user(username="merged-claims", email="merged@example.com")
    oauth_client = _oauth_client()
    expected_claims = {
        "sub": "sub-merged",
        "email": "id-token@example.com",
        "email_verified": True,
        "preferred_username": "id-token-name",
        "groups": ["ops"],
    }
    provider = OidcProvider(userinfo={
        "sub": "sub-merged",
        "email": "userinfo@example.com",
        "preferred_username": "userinfo-name",
        "groups": ["ops"],
    })
    link_state, link_record = oauth_state.issue(
        oauth_client, "https://app.example/callback", user_id=str(link_user.pk), next_path="/link-next", flow="link",
    )
    login_state, login_record = oauth_state.issue(oauth_client, "https://app.example/callback", next_path="/login-next")

    id_claims = {key: value for key, value in expected_claims.items() if key != "groups"}
    with mock_transport(provider):
        provider.id_claims = {**id_claims, "nonce": link_record.nonce}
        link_result = identity.complete_link(
            oauth_client, code="code", state_token=link_state, redirect_uri="https://app.example/callback",
        )
        provider.id_claims = {**id_claims, "nonce": login_record.nonce}
        login_result = identity.complete_login(
            oauth_client, code="code", state_token=login_state, redirect_uri="https://app.example/callback",
        )

    link_result.account.refresh_from_db()
    assert link_result.user.pk == link_user.pk
    assert expected_claims.items() <= link_result.claims.items()
    assert link_result.next_path == "/link-next"
    assert expected_claims.items() <= link_result.account.identity_claims.items()
    assert login_result.user.pk == link_user.pk
    assert login_result.user.backend == identity.SESSION_AUTH_BACKEND
    assert expected_claims.items() <= login_result.claims.items()
    assert login_result.next_path == "/login-next"
    # The verified login establishes the user's own party: a controlled handle + confirmed self-link.
    with system_context(reason="test verify login claim"):
        own_handle = PartiesHandle.objects.get(value="id-token@example.com")
        person = PartiesPerson.objects.for_user(link_user)
        assert own_handle.controller_id == link_user.pk
        assert own_handle.party_id == person.pk


def test_complete_login_does_not_claim_an_unverified_email(db: None) -> None:
    """An OIDC email becomes a parties identity claim only when explicitly verified."""

    del db
    user = get_user_model().objects.create_user(username="unverified-login", email="unverified-account@example.com")
    oauth_client = _oauth_client(slug="unverified-claim")
    ExternalAccount.objects.link(oauth_client, "sub-unverified", owner=user)
    state_token, record = oauth_state.issue(oauth_client, "https://app.example/callback")
    provider = OidcProvider(id_claims={
        "sub": "sub-unverified", "email": "unverified@example.com", "email_verified": False, "nonce": record.nonce,
    })

    with mock_transport(provider):
        completion = identity.complete_login(
            oauth_client, code="code", state_token=state_token, redirect_uri="https://app.example/callback",
        )

    assert completion.user.pk == user.pk
    with system_context(reason="test unverified email stays unclaimed"):
        assert not PartiesHandle.objects.filter(value="unverified@example.com").exists()


def test_complete_login_contains_parties_bookkeeping_failure(
    db: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A parties bookkeeping refusal is logged but never turns a verified login into failure."""

    del db
    user = get_user_model().objects.create_user(username="bookkeeping-login", email="bookkeeping@example.com")
    oauth_client = _oauth_client(slug="bookkeeping-failure")
    ExternalAccount.objects.link(oauth_client, "sub-bookkeeping", owner=user)
    state_token, record = oauth_state.issue(oauth_client, "https://app.example/callback")
    overlong = f"{'x' * 520}@example.com"  # parties refuses a handle longer than its value column
    provider = OidcProvider(id_claims={
        "sub": "sub-bookkeeping", "email": overlong, "email_verified": True, "nonce": record.nonce,
    })

    with mock_transport(provider):
        completion = identity.complete_login(
            oauth_client, code="code", state_token=state_token, redirect_uri="https://app.example/callback",
        )

    assert completion.user.pk == user.pk
    assert "Could not record the verified OIDC email in parties" in caplog.text


def test_complete_link_rejects_account_owned_by_another_user(db: None) -> None:
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
    state_token, record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(other.pk),
        flow="link",
    )
    provider = OidcProvider(id_claims={"sub": "sub-linked", "email": "other@example.com", "nonce": record.nonce})

    with mock_transport(provider), pytest.raises(OAuthFlowError) as exc_info:
        identity.complete_link(
            oauth_client,
            code="code",
            state_token=state_token,
            redirect_uri="https://app.example/callback",
        )

    assert exc_info.value.code == "account_already_linked"
    assert exc_info.value.http_status == 409


def test_complete_link_binds_to_state_user_after_session_swap(db: None) -> None:
    """Account linking uses the start-flow user captured in state, not a later session user."""

    start_user = get_user_model().objects.create_user(username="link-start", email="start@example.com")
    swapped_user = get_user_model().objects.create_user(username="link-swapped", email="swapped@example.com")
    oauth_client = _oauth_client()
    state_token, record = oauth_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(start_user.pk),
        flow="link",
    )
    provider = OidcProvider(id_claims={"sub": "sub-swapped", "email": "start@example.com", "nonce": record.nonce})

    with mock_transport(provider):
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
    return client


@pytest.mark.django_db
def test_oidc_group_by_login_enabled() -> None:
    """OAuth clients group directly by the login-enabled OIDC discriminator.

    Exercises the one-model shape: OIDC login is represented directly by
    ``OAuthClient.login_enabled``.
    """

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


def test_oidc_login_picker_queryset_scope(db: None) -> None:
    """The OIDC extension owns the public login-picker queryset predicate."""

    del db
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


class RefusingProvider(GenericOAuth2):
    """A provider preset whose grant refinement refuses the connection."""

    transient = False

    @classmethod
    def refine_grant(cls, protocol: Any, tokens: dict[str, Any]) -> dict[str, Any]:
        raise IntegrationError("Connection was refused.", transient=cls.transient)


class UnavailableProvider(RefusingProvider):
    """A provider preset whose grant refinement reports a transient outage."""

    transient = True


def _bound_flow(request: Any, client: Any, flow: oauth_state.StateFlow, user: Any = None) -> tuple[str, Any, str]:
    """Start one flow bound to ``request``'s session; return its state token, record and redirect."""

    token, record, redirect, _mode = oauth_flow.issue_flow(
        request, client, "https://app.example/callback", user_id=str(user.pk) if user else None, flow=flow,
    )
    return token, record, redirect


def _complete(schema: Any, request: Any, mutation: str, token: str, redirect: str, fields: str) -> dict[str, Any]:
    """Run one completion mutation for a session-bound flow."""

    document = f'''mutation {{
      {mutation}(code: "code", state: "{token}", redirect_uri: "{redirect}") {{ {fields} }}
    }}'''
    return result_data(execute_schema(schema, document, request=request))[mutation]


def _console_schema() -> Any:
    return GraphQLSchemas([
        SchemaAddon(module.schemas) for module in (iam_schema, integrate_schema, oidc_schema)
    ]).build("console")


@pytest.mark.django_db
def test_oidc_link_refines_the_grant_before_userinfo_and_credential_persistence() -> None:
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={
        "generic_oauth2": "tests.integrate_fixtures.RefiningProvider",
    }), system_context(reason="test refined OIDC link"):
        user = get_user_model().objects.create_user(username="refined-link")
        client = _oauth_client("refined-link-client")
        state_token, record = oauth_state.issue(
            client, "https://app.example/callback", user_id=str(user.pk), flow="link",
        )
        provider = OidcProvider(
            token={"access_token": "short"},
            id_claims={"sub": "linked-subject", "nonce": record.nonce},
            userinfo={"sub": "linked-subject", "name": "Linked identity"},
        )

        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/oauth/token" and request_fields(request)["grant_type"] == "resource_grant":
                assert request_fields(request)["short_token"] == "short"
                # Refinement may replace the ID token; only the code exchange's token proves the login.
                return httpx2.Response(200, json={"access_token": "refined", "id_token": "refined-identity"})
            return provider(request)

        with mock_transport(respond) as requests:
            result = identity.complete_link(
                client, code="code", state_token=state_token, redirect_uri="https://app.example/callback",
            )
        grants = [request_fields(request)["grant_type"] for request in requests if request.url.path == "/oauth/token"]
        [userinfo] = [request for request in requests if request.url.path == "/oauth/userinfo"]
        assert grants == ["authorization_code", "resource_grant"]
        assert userinfo.headers["Authorization"] == "Bearer refined"
        assert userinfo.url.params["proof"] == "refined"
        assert result.account.external_id == "linked-subject"
        assert result.account.display_name == "Linked identity"
        assert Credential.objects.get(user=user, oauth_client=client).secret_value() == "refined"


@pytest.mark.django_db
def test_google_preset_owns_the_subject_bearing_profile_endpoint() -> None:
    with system_context(reason="test Google preset identity"):
        client = OAuthClient.objects.create(slug="google-profile", provider_type="google")
    endpoint = GoogleType.effective_defaults()["userinfo_endpoint"]
    assert endpoint == "https://openidconnect.googleapis.com/v1/userinfo"
    assert client.userinfo_endpoint == endpoint

    with mock_transport(lambda request: httpx2.Response(200, json={"sub": "provider-subject"})) as requests:
        claims = OAuthClientProtocol(client).fetch_userinfo("fake")
    assert [str(request.url) for request in requests] == [endpoint]
    assert client.external_id_from_claims(claims) == "provider-subject"


def test_google_seed_discovers_the_preset_profile_without_a_conflicting_override() -> None:
    seed_file = Path(__file__).parents[1] / "src/angee/integrate/resources/install/010_integrate.oauthclient.yaml"
    seed = next(row for row in yaml.safe_load(seed_file.read_text()) if row["xref"] == "oauth_gemini")
    client = _stub_oauth_client(**{key: value for key, value in seed.items() if key != "xref"})
    endpoint = GoogleType.effective_defaults()["userinfo_endpoint"]

    def respond(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == client.discovery_url:
            return httpx2.Response(200, json={"userinfo_endpoint": endpoint})
        return httpx2.Response(200, json={"sub": "seed-subject"})

    with mock_transport(respond) as requests:
        claims = OAuthClientProtocol(client).fetch_userinfo("fake")
    assert str(requests[-1].url) == endpoint
    assert client.external_id_from_claims(claims) == "seed-subject"


@pytest.mark.django_db
@pytest.mark.parametrize("flow_name", ["login", "link", "connect"])
@pytest.mark.parametrize("request_format", ["form", "json"])
def test_oauth_completion_returns_typed_outage_for_refused_endpoint(flow_name: str, request_format: str) -> None:
    with system_context(reason="test refused provider"):
        user = get_user_model().objects.create_user(username="refused-provider-user")
        client = _oauth_client("refused-provider", token_request_format=request_format)
    request = graphql_request(AnonymousUser() if flow_name == "login" else user, session=True)
    token, _record, redirect = _bound_flow(
        request, client, oauth_state.StateFlow(flow_name), user if flow_name != "login" else None,
    )
    mutation = {"login": "login_complete", "link": "link_account_complete", "connect": "connect_account_complete"}
    with mock_transport(_refuse_dial):
        result = _complete(_console_schema(), request, mutation[flow_name], token, redirect, "error error_code")
    expected_code = "provider_unavailable" if flow_name == "connect" else TOKEN_EXCHANGE_FAILED
    assert result == {"error": OAuthFlowError(expected_code, 503).public_message, "error_code": expected_code}
    with system_context(reason="test refused provider wrote no credential"):
        assert not Credential.objects.filter(user=user, oauth_client=client).exists()


@pytest.mark.django_db
def test_oidc_login_verifies_code_exchange_when_refinement_omits_id_token() -> None:
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={
        "generic_oauth2": "tests.integrate_fixtures.RefiningProvider",
    }), system_context(reason="test nonce proof survives refinement"):
        user = get_user_model().objects.create_user(username="refined-login")
        client = _oauth_client("refined-login-client")
        ExternalAccount.objects.link(client, "login-subject", owner=user)
        token, record = oauth_state.issue(client, "https://app.example/callback")
        provider = OidcProvider(
            token={"access_token": "short"},
            id_claims={"sub": "login-subject", "nonce": record.nonce},
            userinfo={"sub": "login-subject"},
        )

        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/oauth/token" and request_fields(request)["grant_type"] == "resource_grant":
                return httpx2.Response(200, json={"access_token": "refined"})
            return provider(request)

        with mock_transport(respond) as requests:
            completed = identity.complete_login(
                client, code="code", state_token=token, redirect_uri="https://app.example/callback",
            )
        assert completed.user.pk == user.pk
        assert [request_fields(r)["grant_type"] for r in requests if r.url.path == "/oauth/token"] == [
            "authorization_code", "resource_grant",
        ]


def _refuse_dial(request: httpx2.Request) -> httpx2.Response:
    """Answer as ``PinnedTransport`` does when the provider refuses the connection."""

    raise RequestNotSentError()


@pytest.mark.parametrize("operation", ["revoke", "metadata"])
def test_oauth_helpers_map_refused_endpoint_to_transient_flow_error(operation: str) -> None:
    endpoint = f"https://refused-{operation}.example/oauth"
    with mock_transport(_refuse_dial), pytest.raises(OAuthFlowError) as raised:
        if operation == "revoke":
            OAuthClientProtocol(_stub_oauth_client(revoke_endpoint=endpoint)).revoke_token("synthetic-token")
        else:
            oauth_discovery.discovery_document(endpoint)
    assert raised.value.http_status == 503 and raised.value.transient
    assert raised.value.code == (TOKEN_EXCHANGE_FAILED if operation == "revoke" else DISCOVERY_FAILED)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("request_format", ["form", "json"])
def test_blank_secret_public_client_refreshes_credential(request_format: str) -> None:
    with system_context(reason="test public refresh"):
        user = get_user_model().objects.create_user(username="public-refresh")
        client = _oauth_client("public-refresh", client_secret="", token_request_format=request_format)
        credential = Credential.objects.upsert_for_user(
            user, client, "oauth", {"access_token": "old-access", "refresh_token": "old-refresh", "expires_in": 1},
        )
    provider = OidcProvider(token={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})

    with mock_transport(provider) as requests, system_context(reason="test public refresh operation"):
        credential.refresh_now()
        assert credential.reveal()["access_token"] == "new-access"
        assert credential.reveal()["refresh_token"] == "new-refresh"
        assert credential.last_refresh_status == "ok"
        assert not credential.is_reconnect_required
    [request] = requests
    fields = request_fields(request)
    assert (fields["grant_type"], fields["client_id"], fields["refresh_token"]) == (
        "refresh_token", client.client_id, "old-refresh",
    )
    assert "client_secret" not in fields and "authorization" not in request.headers


@pytest.mark.django_db
def test_oidc_login_with_blank_secret_public_client() -> None:
    with system_context(reason="test public OIDC login"):
        user = get_user_model().objects.create_user(username="public-login")
        client = _oauth_client("public-login", client_secret="")
        ExternalAccount.objects.link(client, "public-subject", owner=user)
    request = graphql_request(AnonymousUser(), session=True)
    token, record, redirect = _bound_flow(request, client, oauth_state.StateFlow.LOGIN)
    provider = OidcProvider(id_claims={"sub": "public-subject", "nonce": record.nonce})

    with mock_transport(provider) as requests:
        result = _complete(_console_schema(), request, "login_complete", token, redirect, "ok error error_code")

    assert result == {"ok": True, "error": None, "error_code": None}
    assert request.user.pk == user.pk
    fields = request_fields(requests[0])
    assert fields["client_id"] == client.client_id and fields["code_verifier"] == record.code_verifier
    assert "client_secret" not in fields and "authorization" not in requests[0].headers


@pytest.mark.django_db
@pytest.mark.parametrize("flow_name", ["login", "link"])
@pytest.mark.parametrize("transient", [False, True])
def test_oidc_completion_returns_typed_integration_error(flow_name: str, transient: bool) -> None:
    preset = "UnavailableProvider" if transient else "RefusingProvider"
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={"generic_oauth2": f"tests.test_oidc.{preset}"}):
        with system_context(reason="test OIDC integration refusal"):
            user = get_user_model().objects.create_user(username="integration-refusal")
            client = _oauth_client("integration-refusal")
        request = graphql_request(user if flow_name == "link" else AnonymousUser(), session=True)
        token, _record, redirect = _bound_flow(
            request, client, oauth_state.StateFlow(flow_name), user if flow_name == "link" else None,
        )
        mutation = "login_complete" if flow_name == "login" else "link_account_complete"
        with mock_transport(OidcProvider()):
            result = _complete(_console_schema(), request, mutation, token, redirect, "error error_code")
    assert result == {
        "error": (
            "The provider is temporarily unavailable. Try again later." if transient else "Connection was refused."
        ),
        "error_code": "provider_unavailable" if transient else "connection_refused",
    }


@pytest.mark.django_db
@pytest.mark.parametrize("userinfo_sub", [None, "different-subject", "verified-subject"])
def test_oidc_userinfo_email_link_requires_matching_subject(userinfo_sub: str | None) -> None:
    user = get_user_model().objects.create_user(username="email-link-target", email="target@example.com")
    client = _oauth_client("subject-check", link_on_email_match=True)
    redirect = "https://app.example/callback"
    token, record = oauth_state.issue(client, redirect)
    userinfo = {"email": "target@example.com", "email_verified": True}
    if userinfo_sub is not None:
        userinfo["sub"] = userinfo_sub
    provider = OidcProvider(id_claims={"sub": "verified-subject", "nonce": record.nonce}, userinfo=userinfo)
    with mock_transport(provider):
        if userinfo_sub == "verified-subject":
            completion = identity.complete_login(client, code="code", state_token=token, redirect_uri=redirect)
            assert completion.user.pk == user.pk
            assert completion.claims["email"] == "target@example.com"
        else:
            with pytest.raises(IdentityFlowError):
                identity.complete_login(client, code="code", state_token=token, redirect_uri=redirect)
            with system_context(reason="test untrusted userinfo never linked"):
                assert not ExternalAccount.objects.filter(oauth_client=client).exists()


@pytest.mark.parametrize("domains,allowed", [(["corp.example"], False), ([], True)])
def test_signup_without_email_respects_provider_domain_policy(
    db: None, domains: list[str], allowed: bool,
) -> None:
    client = _oauth_client(create_on_login=True, allowed_email_domains=domains)
    if allowed:
        user = identity.resolve(client, sub="without-email", email=None, claims={"sub": "without-email"})
        assert user.email == ""
    else:
        with pytest.raises(OAuthFlowError) as caught:
            identity.resolve(client, sub="without-email", email=None, claims={"sub": "without-email"})
        assert caught.value.code == IDENTITY_RESOLUTION_FAILED
        assert not ExternalAccount.system_queryset().filter(external_id="without-email").exists()


@pytest.mark.parametrize("unusable", ["revoked", "expired", "disabled"])
def test_disconnect_does_not_count_unusable_oidc_alternatives(db: None, unusable: str) -> None:
    user = get_user_model().objects.create_user("unusable-alternative")
    client = _oauth_client()
    account = ExternalAccount.objects.link(client, "working-subject", owner=user)
    credential = Credential.objects.upsert_for_user(
        user, client, "oauth", {"access_token": "token"}, external_account=account,
    )
    other_client = _oauth_client(slug="other-unusable")
    other = ExternalAccount.objects.link(other_client, "other-subject", owner=user)
    Credential.objects.upsert_for_user(user, other_client, "oauth", {"access_token": "other"}, external_account=other)
    with system_context(reason="test.oidc.unusable_alternative"):
        if unusable == "disabled":
            type(other_client).objects.filter(pk=other_client.pk).update(is_enabled=False)
        else:
            type(other).objects.filter(pk=other.pk).update(status=unusable)
    with transaction.atomic(), pytest.raises(ValidationError) as caught:
        credential.check_disconnect()
    assert caught.value.code == "only_sign_in_method"

@pytest.mark.parametrize("selected_status", ["revoked", "expired"])
def test_disconnect_unusable_account_preserves_the_active_alternative(db, selected_status) -> None:
    user = get_user_model().objects.create_user("disconnect-unusable-account")
    client = _oauth_client()
    selected = ExternalAccount.objects.link(client, "unusable-selected", owner=user)
    credential = Credential.objects.upsert_for_user(
        user, client, "oauth", {"access_token": "selected"}, external_account=selected,
    )
    alternative_client = _oauth_client(slug="usable-alternative")
    alternative = ExternalAccount.objects.link(alternative_client, "active-alternative", owner=user)
    kept = Credential.objects.upsert_for_user(
        user, alternative_client, "oauth", {"access_token": "alternative"}, external_account=alternative,
    )
    with system_context(reason="test.oidc.disconnect_unusable"):
        type(selected).objects.filter(pk=selected.pk).update(status=selected_status)
    assert Credential.objects.disconnect_account(user, selected.sqid)
    with system_context(reason="test.oidc.disconnect_unusable_result"):
        assert not Credential.objects.filter(pk=credential.pk).exists()
        assert Credential.objects.filter(pk=kept.pk).exists()
