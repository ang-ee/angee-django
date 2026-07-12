"""Tests for the OAuth/OIDC protocol, connect flow, and OIDC login resolution."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import timedelta
from types import MethodType, SimpleNamespace
from typing import Any
from urllib import parse

import httpx
import pytest
from asgiref.sync import async_to_sync, sync_to_async
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rebac import system_context
from strawberry_django_aggregates import AggregateOp, compute_aggregation

from angee.iam_integrate_oidc import identity
from angee.iam_integrate_oidc import protocol as oidc_protocol
from angee.iam_integrate_oidc.identity import IDENTITY_RESOLUTION_FAILED
from angee.iam_integrate_oidc.models import OAuthClientOidc
from angee.iam_integrate_oidc.protocol import OAuthClientOidcProtocol
from angee.integrate.connect import complete_account_connect
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
from tests.conftest import (
    Credential,
    ExternalAccount,
    OAuthClient,
    _create_missing_tables,
)


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

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, json={"access_token": "access-token"})

    transport = httpx.MockTransport(handler)

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

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["fields"] = json.loads(request.content)
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(
            200,
            json={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "ignored": "not-token-material",
            },
        )

    oauth_client = _stub_oauth_client(
        supports_pkce=True,
        token_request_format="json",
        token_param_values={"audience": "https://api.example"},
    )
    protocol = OAuthClientProtocol(oauth_client)
    protocol._transport = httpx.MockTransport(handler)

    tokens = protocol.exchange_code(
        code="auth-code",
        redirect_uri="https://app.example/callback",
        code_verifier="verifier",
        state="state-token",
    )

    assert tokens == {"access_token": "access-token", "refresh_token": "refresh-token"}
    assert captured["url"] == "https://issuer.example/oauth/token"
    assert captured["ua"] == oauth_protocol.USER_AGENT
    assert captured["fields"] == {
        "audience": "https://api.example",
        "client_id": "oidc-client",
        "client_secret": "secret",
        "code": "auth-code",
        "code_verifier": "verifier",
        "grant_type": "authorization_code",
        "redirect_uri": "https://app.example/callback",
        "state": "state-token",
    }


def test_refresh_token_posts_refresh_grant() -> None:
    """`refresh_token` posts a ``refresh_token`` grant and returns the renewed material."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["fields"] = json.loads(request.content)
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "rotated", "expires_in": 7200})

    oauth_client = _stub_oauth_client(token_request_format="json")
    protocol = OAuthClientProtocol(oauth_client)
    protocol._transport = httpx.MockTransport(handler)

    tokens = protocol.refresh_token(refresh_token="stored-refresh")

    assert tokens == {"access_token": "new-access", "refresh_token": "rotated", "expires_in": 7200}
    assert captured["url"] == "https://issuer.example/oauth/token"
    assert captured["fields"] == {
        "client_id": "oidc-client",
        "client_secret": "secret",
        "grant_type": "refresh_token",
        "refresh_token": "stored-refresh",
    }


def test_exchange_code_form_path_maps_non_json_error_to_oauth_flow_error() -> None:
    """A non-JSON token error on the default form path surfaces as ``OAuthFlowError``.

    Authlib parses the token body as JSON before checking the status, so a provider/CDN
    edge that answers a non-JSON 4xx (the documented Anthropic 403/429 block page) raises
    a ``ValueError`` inside Authlib; it must map to the stable seam, not leak a 500.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>403 Forbidden</html>")

    protocol = OAuthClientProtocol(_stub_oauth_client())
    protocol._transport = httpx.MockTransport(handler)

    with pytest.raises(OAuthFlowError) as exc_info:
        protocol.exchange_code(code="auth-code", redirect_uri="https://app.example/callback")

    assert exc_info.value.code == TOKEN_EXCHANGE_FAILED


def test_refresh_token_form_path_returns_renewed_material() -> None:
    """The default (form) refresh path returns the filtered token material."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600})

    protocol = OAuthClientProtocol(_stub_oauth_client())
    protocol._transport = httpx.MockTransport(handler)

    tokens = protocol.refresh_token(refresh_token="stored-refresh")

    assert tokens == {"access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600}


def test_exchange_code_public_client_posts_client_id_and_verifier() -> None:
    """A public client (no secret) still posts ``client_id`` and the PKCE ``code_verifier``
    (RFC 7636) and never a ``client_secret``."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = dict(parse.parse_qsl(request.content.decode()))
        return httpx.Response(200, json={"access_token": "access-token"})

    protocol = OAuthClientProtocol(_stub_oauth_client(client_secret="", supports_pkce=True))
    protocol._transport = httpx.MockTransport(handler)

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
    assert "state" not in body
    assert "client_secret" not in body


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

    class FakeHttpResponse:
        status = 200
        ok = True

        def json(self) -> dict[str, list[object]]:
            return {"keys": []}

    class FakeHttpClient:
        def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
            allow_private: bool,
            timeout: int,
            **kwargs: object,
        ) -> FakeHttpResponse:
            del kwargs
            requests.append((url, headers, allow_private, timeout))
            return FakeHttpResponse()

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
    oidc_tables: None,
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
def test_resolver_blocks_non_active_account(oidc_tables: None) -> None:
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
def test_external_account_manager_coerces_status_member_names(oidc_tables: None) -> None:
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
def test_resolver_blocks_inactive_user(oidc_tables: None) -> None:
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
def test_resolver_blocks_existing_external_account_with_service_owner(oidc_tables: None) -> None:
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
    oidc_tables: None,
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
    oidc_tables: None,
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
    oidc_tables: None,
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
    oidc_tables: None,
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


def test_oidc_email_match_fails_loud_without_people_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OIDC email-match path must use the user owner's people() scope directly."""

    class QuerySet:
        def filter(self, **kwargs: object) -> QuerySet:
            return self

        def order_by(self, *fields: str) -> QuerySet:
            return self

        def __getitem__(self, key: object) -> list[object]:
            return []

    class Manager:
        def all(self) -> QuerySet:
            return QuerySet()

    class UserModel:
        objects = Manager()

    monkeypatch.setattr(identity, "get_user_model", lambda: UserModel)
    resolver = object.__new__(identity.OidcIdentityResolver)

    with pytest.raises(AttributeError, match="people"):
        resolver._find_by_email("someone@example.com")


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_rejects_ambiguous_email(
    oidc_tables: None,
) -> None:
    """Email-match login fails closed when more than one user owns the email."""

    user_model = get_user_model()
    user_model.objects.create_user(username="email-match-a", email="dupe@example.com")
    user_model.objects.create_user(username="email-match-b", email="DUPE@example.com")
    oauth_client = _oauth_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    with pytest.raises(OAuthFlowError):
        identity.resolve(
            oauth_client,
            sub="sub-dupe-email",
            email="dupe@example.com",
            claims={"sub": "sub-dupe-email", "email": "dupe@example.com", "email_verified": True},
        )

    with system_context(reason="test oidc assertions"):
        assert not ExternalAccount.objects.filter(oauth_client=oauth_client, external_id="sub-dupe-email").exists()


@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_provisions_user_and_external_account(
    oidc_tables: None,
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
    oidc_tables: None,
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
    oidc_tables: None,
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
    oidc_tables: None,
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
    oidc_tables: None,
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
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token: {})

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
    oidc_tables: None,
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
    monkeypatch.setattr(OAuthClientProtocol, "fetch_userinfo", lambda self, access_token: claims)
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
def test_complete_account_connect_rejects_missing_stable_external_id(
    oidc_tables: None,
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
        lambda self, access_token: {"email": "missing@example.com"},
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
    oidc_tables: None,
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
def test_credential_disconnect_guard_blocks_last_oidc_sign_in(
    oidc_tables: None,
) -> None:
    """Explicit disconnect refuses the only sign-in method for a passwordless user."""

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

    with pytest.raises(OAuthFlowError) as exc_info:
        Credential.objects.check_disconnect(credential)

    assert exc_info.value.code == "only_sign_in_method"
    assert exc_info.value.http_status == 409


@pytest.mark.django_db(transaction=True)
def test_low_level_credential_delete_does_not_run_disconnect_guard(
    oidc_tables: None,
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
    oidc_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Userinfo claims enrich ID-token claims before login resolve and account link."""

    link_user = get_user_model().objects.create_user(username="merged-claims", email="merged@example.com")
    oauth_client = _oauth_client()
    token_response = {"access_token": "access", "id_token": "id-token"}
    id_claims = {
        "sub": "sub-merged",
        "email": "id-token@example.com",
        "preferred_username": "id-token-name",
    }
    userinfo_claims = {
        "email": "userinfo@example.com",
        "preferred_username": "userinfo-name",
        "groups": ["ops"],
    }
    expected_claims = {
        "sub": "sub-merged",
        "email": "id-token@example.com",
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
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token: userinfo_claims)
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


@pytest.mark.django_db(transaction=True)
def test_complete_link_rejects_account_owned_by_another_user(
    oidc_tables: None,
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
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token: {})

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
    oidc_tables: None,
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
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, access_token: {})

    result = identity.complete_link(
        oauth_client,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/callback",
    )
    account = result.account

    with system_context(reason="test oidc assertions"):
        owner = ExternalAccount.objects.owner_for(account)
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


@pytest.fixture()
def oidc_tables() -> Iterator[None]:
    """Create concrete connection test tables for one test."""

    created_models = _create_missing_tables()
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


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

    created_models = _create_missing_tables()
    try:
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
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_oidc_login_picker_queryset_scope(oidc_tables: None) -> None:
    """The OIDC extension owns the public login-picker queryset predicate."""

    del oidc_tables
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
