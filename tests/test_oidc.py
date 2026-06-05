"""Tests for IAM OIDC helpers and identity resolution."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from urllib import parse

import pytest
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.iam import identity
from angee.iam.models import AccountStatus, CredentialStatus
from angee.iam.oidc import client as oidc_client
from angee.iam.oidc import state as oidc_state
from angee.iam.oidc.errors import (
    IDENTITY_RESOLUTION_FAILED,
    INVALID_ID_TOKEN,
    INVALID_STATE,
    OidcFlowError,
)
from tests.conftest import Credential, ExternalAccount, OAuthClient, Vendor, _create_missing_tables


def test_discovery_fallback_fills_blank_authorize_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank authorization endpoint is loaded from discovery."""

    calls: list[str] = []
    oauth_client = _stub_oauth_client(
        authorize_endpoint="",
        discovery_url="https://issuer.example/.well-known/openid-configuration",
    )

    def get_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        del headers
        calls.append(url)
        return {"authorization_endpoint": "https://issuer.example/oauth/authorize"}

    monkeypatch.setattr(oidc_client, "_get_json", get_json)

    url = oidc_client.build_authorize_url(
        oauth_client,
        state="state-token",
        nonce="nonce-token",
        redirect_uri="https://app.example/callback",
        scopes=("openid", "email"),
    )

    assert calls == ["https://issuer.example/.well-known/openid-configuration"]
    assert oauth_client.authorize_endpoint == "https://issuer.example/oauth/authorize"
    assert url.startswith("https://issuer.example/oauth/authorize?")


def test_fetch_discovery_caches_document_by_discovery_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery cache hits avoid a second discovery document fetch."""

    discovery_url = "https://cached.example/.well-known/openid-configuration"
    oauth_client = _stub_oauth_client(
        issuer="",
        authorize_endpoint="",
        token_endpoint="",
        userinfo_endpoint="",
        jwks_uri="",
        discovery_url=discovery_url,
    )
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

    def get_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        del headers
        fetches.append(url)
        return {
            "issuer": "https://cached.example",
            "authorization_endpoint": "https://cached.example/oauth/authorize",
            "token_endpoint": "https://cached.example/oauth/token",
            "userinfo_endpoint": "https://cached.example/oauth/userinfo",
            "jwks_uri": "https://cached.example/oauth/jwks",
        }

    monkeypatch.setattr(oidc_client.cache, "get", cache_get)
    monkeypatch.setattr(oidc_client.cache, "set", cache_set)
    monkeypatch.setattr(oidc_client, "_get_json", get_json)

    first = oidc_client.fetch_discovery(oauth_client)
    second = oidc_client.fetch_discovery(oauth_client)

    assert first == second
    assert fetches == [discovery_url]
    assert len(cache_gets) == 2
    assert len(cache_sets) == 1
    assert cache_sets[0][1] == 3600


def test_oidc_http_helpers_send_browser_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloudflare-fronted IdPs should not see urllib's default user agent."""

    requests: list[Any] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def urlopen(req: Any, timeout: int) -> Response:
        assert timeout == oidc_client.HTTP_TIMEOUT_SECONDS
        requests.append(req)
        return Response()

    monkeypatch.setattr(oidc_client.request, "urlopen", urlopen)

    oidc_client._get_json("https://idp.example/.well-known/openid-configuration")
    oidc_client._post_form("https://idp.example/token", {"code": "abc"})

    assert [req.get_header("User-agent") for req in requests] == [
        oidc_client._BROWSER_USER_AGENT,
        oidc_client._BROWSER_USER_AGENT,
    ]


def test_authorize_url_contains_state_nonce_and_pkce() -> None:
    """Authorize URL includes state, nonce, and PKCE parameters when supported."""

    oauth_client = _stub_oauth_client(supports_pkce=True)
    state_token, record = oidc_state.issue(oauth_client, "https://app.example/callback")
    url = oidc_client.build_authorize_url(
        oauth_client,
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
    """Authorize URL adds the required OIDC scope when configured scopes omit it."""

    url = oidc_client.build_authorize_url(
        _stub_oauth_client(),
        state="state-token",
        nonce="nonce-token",
        redirect_uri="https://app.example/callback",
        scopes=("email", "profile"),
    )
    query = parse.parse_qs(parse.urlsplit(url).query)

    assert query["scope"] == ["openid email profile"]


def test_verify_id_token_rejects_bad_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched issuer claim."""

    oauth_client = _stub_oauth_client()
    monkeypatch.setattr(
        oidc_client.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "https://wrong.example",
            "aud": oauth_client.client_id,
            "nonce": "nonce",
        },
    )

    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(oauth_client, "token", nonce="nonce", _jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_rejects_bad_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched audience claim."""

    oauth_client = _stub_oauth_client()
    monkeypatch.setattr(
        oidc_client.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": oauth_client.issuer, "aud": "other-client", "nonce": "nonce"},
    )

    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(oauth_client, "token", nonce="nonce", _jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_rejects_bad_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched nonce claim."""

    oauth_client = _stub_oauth_client()
    monkeypatch.setattr(
        oidc_client.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": oauth_client.issuer, "aud": oauth_client.client_id, "nonce": "wrong"},
    )

    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(oauth_client, "token", nonce="nonce", _jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_accepts_rs256_and_rejects_ps256() -> None:
    """ID token verification accepts only the configured asymmetric algorithms."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    oauth_client = _stub_oauth_client()
    now = int(time.time())
    claims = {
        "aud": oauth_client.client_id,
        "exp": now + 60,
        "iat": now,
        "iss": oauth_client.issuer,
        "nonce": "nonce",
        "sub": "sub-alg",
    }
    rs256_token = oidc_client.jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "kid"},
    )
    ps256_token = oidc_client.jwt.encode(
        claims,
        private_key,
        algorithm="PS256",
        headers={"kid": "kid"},
    )
    jwks_client = _FakeJwksClient(private_key.public_key())

    verified = oidc_client.verify_id_token(
        oauth_client,
        rs256_token,
        nonce="nonce",
        _jwks_client=jwks_client,
    )

    assert verified["sub"] == "sub-alg"
    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(
            oauth_client,
            ps256_token,
            nonce="nonce",
            _jwks_client=jwks_client,
        )
    assert exc_info.value.code == INVALID_ID_TOKEN


@pytest.mark.django_db(transaction=True)
def test_resolver_existing_external_account_returns_owner(
    oidc_tables: None,
) -> None:
    """An existing external account resolves through its owner relationship."""

    user = get_user_model().objects.create_user(username="oidc-owner", email="owner@example.com")
    vendor, oauth_client = _vendor_and_oauth_client()
    account = ExternalAccount.objects.link(
        vendor,
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
    vendor, oauth_client = _vendor_and_oauth_client()
    ExternalAccount.objects.link(
        vendor,
        "sub-revoked",
        owner=user,
        email="rev@example.com",
        status=AccountStatus.REVOKED,
    )

    with pytest.raises(OidcFlowError):
        identity.resolve(oauth_client, sub="sub-revoked", email="rev@example.com", claims={"sub": "sub-revoked"})


@pytest.mark.django_db(transaction=True)
def test_resolver_blocks_inactive_user(oidc_tables: None) -> None:
    """A deactivated owner must not authenticate via OIDC (parity with the password path)."""

    user = get_user_model().objects.create_user(username="inactive-owner", email="ina@example.com")
    user.is_active = False
    user.save(update_fields=["is_active"])
    vendor, oauth_client = _vendor_and_oauth_client()
    ExternalAccount.objects.link(vendor, "sub-inactive", owner=user, email="ina@example.com")

    with pytest.raises(OidcFlowError):
        identity.resolve(oauth_client, sub="sub-inactive", email="ina@example.com", claims={"sub": "sub-inactive"})


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_uses_signed_email_claim(
    oidc_tables: None,
) -> None:
    """link_on_email_match follows the P1 policy without requiring email_verified."""

    user = get_user_model().objects.create_user(username="verify-match", email="vm@example.com")
    vendor, oauth_client = _vendor_and_oauth_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    resolved = identity.resolve(
        oauth_client,
        sub="sub-unverified",
        email="vm@example.com",
        claims={"sub": "sub-unverified", "email": "vm@example.com"},
    )

    assert resolved.pk == user.pk
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(vendor=vendor, external_id="sub-unverified")
    assert account.email == "vm@example.com"

@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_uses_signed_email_claim(
    oidc_tables: None,
) -> None:
    """create_on_login follows the P1 policy without requiring email_verified."""

    vendor, oauth_client = _vendor_and_oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = identity.resolve(
        oauth_client,
        sub="sub-unverified-new",
        email="new@example.com",
        claims={"sub": "sub-unverified-new", "email": "new@example.com"},
    )

    assert user.email == "new@example.com"
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(vendor=vendor, external_id="sub-unverified-new")
    assert account.email == "new@example.com"

@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_creates_external_account(
    oidc_tables: None,
) -> None:
    """Email-match login links a new external account to an existing user."""

    user = get_user_model().objects.create_user(username="email-match", email="match@example.com")
    vendor, oauth_client = _vendor_and_oauth_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    resolved = identity.resolve(
        oauth_client,
        sub="sub-email",
        email="match@example.com",
        claims={"sub": "sub-email", "email": "match@example.com", "email_verified": True},
    )

    assert resolved.pk == user.pk
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(vendor=vendor, external_id="sub-email")
    assert account.email == "match@example.com"


@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_provisions_user_and_external_account(
    oidc_tables: None,
) -> None:
    """Create-on-login provisions a non-superuser user and linked account."""

    vendor, oauth_client = _vendor_and_oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = identity.resolve(
        oauth_client,
        sub="sub-new",
        email="new@example.com",
        claims={"sub": "sub-new", "email": "new@example.com", "name": "New User", "email_verified": True},
    )

    assert user.email == "new@example.com"
    assert user.is_superuser is False
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(vendor=vendor, external_id="sub-new")
    assert account.email == "new@example.com"
    assert account.display_name == "New User"


@pytest.mark.django_db(transaction=True)
def test_resolver_create_on_login_sets_user_names_from_claims(
    oidc_tables: None,
) -> None:
    """Provisioned users receive first and last names from OIDC name claims."""

    _vendor, oauth_client = _vendor_and_oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

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

    vendor, oauth_client = _vendor_and_oauth_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = async_to_sync(identity.aresolve)(
        oauth_client,
        sub="sub-async",
        email="async@example.com",
        claims={"sub": "sub-async", "email": "async@example.com", "name": "Async User", "email_verified": True},
    )

    assert user.email == "async@example.com"
    assert user.is_superuser is False
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(vendor=vendor, external_id="sub-async")
    assert account.email == "async@example.com"
    assert account.display_name == "Async User"


@pytest.mark.django_db(transaction=True)
def test_resolver_disallowed_domain_raises_403(
    oidc_tables: None,
) -> None:
    """Domain policy blocks linking and provisioning."""

    _vendor, oauth_client = _vendor_and_oauth_client(
        link_on_email_match=True,
        create_on_login=True,
        allowed_email_domains=["allowed.example"],
    )

    with pytest.raises(OidcFlowError) as exc_info:
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
    _vendor, oauth_client = _vendor_and_oauth_client()
    state_token, _record = oidc_state.issue(
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
    monkeypatch.setattr(identity.client_module, "exchange_code", lambda *args, **kwargs: tokens)
    monkeypatch.setattr(
        identity.client_module,
        "verify_id_token",
        lambda *args, **kwargs: {"sub": "sub-token-fields", "email": "tokens@example.com"},
    )
    monkeypatch.setattr(identity.client_module, "fetch_userinfo", lambda *args, **kwargs: {})

    before = timezone.now()
    result = identity.complete_link(
        oauth_client,
        link_user,
        code="code",
        state_token=state_token,
        redirect_uri="https://app.example/callback",
    )
    account = result.account

    with system_context(reason="test oidc assertions"):
        credential = Credential.objects.get(user=link_user, oauth_client=oauth_client, external_account=account)
    account.refresh_from_db()
    assert account.credentials_provider_id == oauth_client.pk
    assert account.credential_id == credential.pk
    assert account.credential_status == "active"
    assert credential.expires_at is not None
    assert before + timedelta(seconds=119) <= credential.expires_at <= timezone.now() + timedelta(seconds=121)
    assert credential.granted_scopes == ["openid", "email", "profile"]
    assert credential.last_refresh_at is not None
    assert credential.last_refresh_status == "ok"
    assert credential.reveal()["refresh_token"] == "refresh-token"


@pytest.mark.django_db(transaction=True)
def test_credential_upsert_reasserts_active_status(
    oidc_tables: None,
) -> None:
    """Re-upserting an OAuth credential reactivates a previously revoked row."""

    user = get_user_model().objects.create_user(username="reactivated", email="reactivated@example.com")
    _vendor, oauth_client = _vendor_and_oauth_client()
    credential = Credential.objects.upsert_for_user(user, oauth_client, "oauth", {"access_token": "first-token"})
    with system_context(reason="test oidc setup"):
        Credential.objects.filter(pk=credential.pk).update(status=CredentialStatus.REVOKED)

    updated = Credential.objects.upsert_for_user(user, oauth_client, "oauth", {"access_token": "second-token"})

    updated.refresh_from_db()
    assert updated.status == CredentialStatus.ACTIVE
    assert updated.reveal()["access_token"] == "second-token"


@pytest.mark.django_db(transaction=True)
def test_userinfo_claims_merge_into_login_and_link_claims(
    oidc_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Userinfo claims enrich ID-token claims before login resolve and account link."""

    link_user = get_user_model().objects.create_user(username="merged-claims", email="merged@example.com")
    _vendor, oauth_client = _vendor_and_oauth_client()
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

    monkeypatch.setattr(identity.client_module, "exchange_code", lambda *args, **kwargs: token_response)
    monkeypatch.setattr(identity.client_module, "verify_id_token", lambda *args, **kwargs: id_claims)
    monkeypatch.setattr(identity.client_module, "fetch_userinfo", lambda *args, **kwargs: userinfo_claims)
    monkeypatch.setattr(identity, "resolve", resolve_user)

    login_state, _login_record = oidc_state.issue(
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
    assert login_result.claims == expected_claims
    assert login_result.next_path == "/login-next"
    assert captured["oauth_client"] == oauth_client
    assert captured["sub"] == "sub-merged"
    assert captured["email"] == "id-token@example.com"
    assert captured["claims"] == expected_claims

    link_state, _link_record = oidc_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(link_user.pk),
        next_path="/link-next",
        flow="link",
    )
    link_result = identity.complete_link(
        oauth_client,
        link_user,
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
    vendor, oauth_client = _vendor_and_oauth_client()
    ExternalAccount.objects.link(
        vendor,
        "sub-linked",
        owner=owner,
        email="owner@example.com",
        identity_claims={"sub": "sub-linked"},
    )
    state_token, _record = oidc_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(other.pk),
        flow="link",
    )
    monkeypatch.setattr(
        identity.client_module,
        "exchange_code",
        lambda *args, **kwargs: {"access_token": "access", "id_token": "id-token"},
    )
    monkeypatch.setattr(
        identity.client_module,
        "verify_id_token",
        lambda *args, **kwargs: {"sub": "sub-linked", "email": "other@example.com"},
    )
    monkeypatch.setattr(identity.client_module, "fetch_userinfo", lambda *args, **kwargs: {})

    with pytest.raises(OidcFlowError) as exc_info:
        identity.complete_link(
            oauth_client,
            other,
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
    vendor, oauth_client = _vendor_and_oauth_client()
    state_token, _record = oidc_state.issue(
        oauth_client,
        "https://app.example/callback",
        user_id=str(start_user.pk),
        flow="link",
    )
    monkeypatch.setattr(
        identity.client_module,
        "exchange_code",
        lambda *args, **kwargs: {"access_token": "access", "id_token": "id-token"},
    )
    monkeypatch.setattr(
        identity.client_module,
        "verify_id_token",
        lambda *args, **kwargs: {"sub": "sub-swapped", "email": "start@example.com"},
    )
    monkeypatch.setattr(identity.client_module, "fetch_userinfo", lambda *args, **kwargs: {})

    result = identity.complete_link(
        oauth_client,
        swapped_user,
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
    state_token, record = oidc_state.issue(oauth_client, "https://app.example/callback")

    assert record.nonce != state_token
    assert oidc_state.consume(state_token) == record
    with pytest.raises(OidcFlowError) as exc_info:
        oidc_state.consume(state_token)

    assert exc_info.value.code == INVALID_STATE
    assert exc_info.value.http_status == 400


def test_state_flow_binding_rejects_cross_flow_completion() -> None:
    """A login token cannot complete a link, and a link token cannot complete a login."""

    oauth_client = SimpleNamespace(sqid="clt_flow", pk=7, supports_pkce=False)

    login_token, _login = oidc_state.issue(
        oauth_client, "https://app.example/callback", flow="login"
    )
    with pytest.raises(OidcFlowError) as link_exc:
        identity.complete_link(
            oauth_client,
            code="code",
            state_token=login_token,
            redirect_uri="https://app.example/callback",
        )
    assert link_exc.value.code == INVALID_STATE

    link_token, _link = oidc_state.issue(
        oauth_client, "https://app.example/callback", user_id="1", flow="link"
    )
    with pytest.raises(OidcFlowError) as login_exc:
        identity.complete_login(
            oauth_client,
            code="code",
            state_token=link_token,
            redirect_uri="https://app.example/callback",
        )
    assert login_exc.value.code == INVALID_STATE


@pytest.fixture()
def oidc_tables() -> Iterator[None]:
    """Create concrete OIDC test tables for one test."""

    created_models = _create_missing_tables()
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _vendor_and_oauth_client(**overrides: Any) -> tuple[Vendor, OAuthClient]:
    """Create one enabled OIDC OAuth client for resolver tests."""

    defaults = {
        "display_name": "OIDC test",
        "client_id": "oidc-client",
        "client_secret": "secret",
        "issuer": "https://issuer.example",
        "authorize_endpoint": "https://issuer.example/oauth/authorize",
        "token_endpoint": "https://issuer.example/oauth/token",
        "userinfo_endpoint": "https://issuer.example/oauth/userinfo",
        "jwks_uri": "https://issuer.example/oauth/jwks",
        "is_oidc": True,
        "is_enabled": True,
        "supports_pkce": True,
        "link_on_email_match": False,
        "create_on_login": False,
        "allowed_email_domains": [],
    }
    defaults.update(overrides)
    with system_context(reason="test oidc setup"):
        vendor = Vendor.objects.create(slug="oidc", display_name="OIDC")
        oauth_client = OAuthClient.objects.create(vendor=vendor, **defaults)
    return vendor, oauth_client


def _stub_oauth_client(**overrides: Any) -> SimpleNamespace:
    """Return an OAuth-client-like object for protocol-helper tests."""

    defaults = {
        "client_id": "oidc-client",
        "client_secret": "secret",
        "issuer": "https://issuer.example",
        "authorize_endpoint": "https://issuer.example/oauth/authorize",
        "token_endpoint": "https://issuer.example/oauth/token",
        "revoke_endpoint": "",
        "userinfo_endpoint": "https://issuer.example/oauth/userinfo",
        "jwks_uri": "https://issuer.example/oauth/jwks",
        "discovery_url": "",
        "supports_pkce": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _FakeJwksClient:
    """JWKS client test double returning a stable signing key."""

    def __init__(self, key: object = "secret") -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        """Return the key object shape PyJWT exposes."""

        del token
        return SimpleNamespace(key=self.key)
