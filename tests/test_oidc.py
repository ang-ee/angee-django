"""Tests for IAM OIDC helpers and identity resolution."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from urllib import parse

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.iam import identity
from angee.iam.oidc import client as oidc_client
from angee.iam.oidc import state as oidc_state
from angee.iam.oidc.errors import (
    IDENTITY_RESOLUTION_FAILED,
    INVALID_ID_TOKEN,
    INVALID_STATE,
    OidcFlowError,
)
from tests.conftest import Client, ExternalAccount, Vendor, _create_missing_tables


def test_discovery_fallback_fills_blank_authorize_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank authorization endpoint is loaded from discovery."""

    calls: list[str] = []
    client = _stub_client(
        authorize_endpoint="",
        discovery_url="https://issuer.example/.well-known/openid-configuration",
    )

    def get_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        del headers
        calls.append(url)
        return {"authorization_endpoint": "https://issuer.example/oauth/authorize"}

    monkeypatch.setattr(oidc_client, "_get_json", get_json)

    url = oidc_client.build_authorize_url(
        client,
        state="state-token",
        nonce="nonce-token",
        redirect_uri="https://app.example/callback",
        scopes=("openid", "email"),
    )

    assert calls == ["https://issuer.example/.well-known/openid-configuration"]
    assert client.authorize_endpoint == "https://issuer.example/oauth/authorize"
    assert url.startswith("https://issuer.example/oauth/authorize?")


def test_authorize_url_contains_state_nonce_and_pkce() -> None:
    """Authorize URL includes state, nonce, and PKCE parameters when supported."""

    client = _stub_client(supports_pkce=True)
    state_token, record = oidc_state.issue(client, "https://app.example/callback")
    url = oidc_client.build_authorize_url(
        client,
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


def test_verify_id_token_rejects_bad_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched issuer claim."""

    client = _stub_client()
    monkeypatch.setattr(
        oidc_client.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "https://wrong.example",
            "aud": client.client_id,
            "nonce": "nonce",
        },
    )

    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(client, "token", nonce="nonce", _jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_rejects_bad_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched audience claim."""

    client = _stub_client()
    monkeypatch.setattr(
        oidc_client.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": client.issuer, "aud": "other-client", "nonce": "nonce"},
    )

    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(client, "token", nonce="nonce", _jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


def test_verify_id_token_rejects_bad_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    """ID token verification rejects a mismatched nonce claim."""

    client = _stub_client()
    monkeypatch.setattr(
        oidc_client.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": client.issuer, "aud": client.client_id, "nonce": "wrong"},
    )

    with pytest.raises(OidcFlowError) as exc_info:
        oidc_client.verify_id_token(client, "token", nonce="nonce", _jwks_client=_FakeJwksClient())

    assert exc_info.value.code == INVALID_ID_TOKEN


@pytest.mark.django_db(transaction=True)
def test_resolver_existing_external_account_returns_owner(
    oidc_tables: None,
) -> None:
    """An existing external account resolves through its owner relationship."""

    user = get_user_model().objects.create_user(username="oidc-owner", email="owner@example.com")
    vendor, client = _vendor_and_client()
    account = ExternalAccount.objects.link(
        vendor,
        "sub-existing",
        owner=user,
        email="owner@example.com",
        identity_claims={"sub": "sub-existing"},
    )

    resolved = identity.resolve(
        client,
        sub="sub-existing",
        email="owner@example.com",
        claims={"sub": "sub-existing"},
    )

    assert resolved.pk == user.pk
    assert account.pk is not None


@pytest.mark.django_db(transaction=True)
def test_resolver_link_on_email_match_creates_external_account(
    oidc_tables: None,
) -> None:
    """Email-match login links a new external account to an existing user."""

    user = get_user_model().objects.create_user(username="email-match", email="match@example.com")
    vendor, client = _vendor_and_client(link_on_email_match=True, allowed_email_domains=["example.com"])

    resolved = identity.resolve(
        client,
        sub="sub-email",
        email="match@example.com",
        claims={"sub": "sub-email", "email": "match@example.com"},
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

    vendor, client = _vendor_and_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = identity.resolve(
        client,
        sub="sub-new",
        email="new@example.com",
        claims={"sub": "sub-new", "email": "new@example.com", "name": "New User"},
    )

    assert user.email == "new@example.com"
    assert user.is_superuser is False
    with system_context(reason="test oidc assertions"):
        account = ExternalAccount.objects.get(vendor=vendor, external_id="sub-new")
    assert account.email == "new@example.com"
    assert account.display_name == "New User"


@pytest.mark.django_db(transaction=True)
def test_async_resolver_create_on_login_provisions_user_and_external_account(
    oidc_tables: None,
) -> None:
    """The ASGI-facing resolver path provisions through thread-sensitive sync ORM."""

    vendor, client = _vendor_and_client(create_on_login=True, allowed_email_domains=["example.com"])

    user = async_to_sync(identity.aresolve)(
        client,
        sub="sub-async",
        email="async@example.com",
        claims={"sub": "sub-async", "email": "async@example.com", "name": "Async User"},
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

    _vendor, client = _vendor_and_client(
        link_on_email_match=True,
        create_on_login=True,
        allowed_email_domains=["allowed.example"],
    )

    with pytest.raises(OidcFlowError) as exc_info:
        identity.resolve(
            client,
            sub="sub-blocked",
            email="blocked@example.com",
            claims={"sub": "sub-blocked", "email": "blocked@example.com"},
        )

    assert exc_info.value.code == IDENTITY_RESOLUTION_FAILED
    assert exc_info.value.http_status == 403


@pytest.mark.django_db(transaction=True)
def test_complete_link_rejects_account_owned_by_another_user(
    oidc_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account linking fails when the external account belongs to another user."""

    owner = get_user_model().objects.create_user(username="linked-owner", email="owner@example.com")
    other = get_user_model().objects.create_user(username="linked-other", email="other@example.com")
    vendor, client = _vendor_and_client()
    ExternalAccount.objects.link(
        vendor,
        "sub-linked",
        owner=owner,
        email="owner@example.com",
        identity_claims={"sub": "sub-linked"},
    )
    state_token, _record = oidc_state.issue(client, "https://app.example/callback")
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

    with pytest.raises(OidcFlowError) as exc_info:
        identity.complete_link(
            client,
            other,
            code="code",
            state_token=state_token,
            redirect_uri="https://app.example/callback",
        )

    assert exc_info.value.code == "account_already_linked"
    assert exc_info.value.http_status == 409


def test_state_records_are_single_use() -> None:
    """Consumed state records cannot be consumed again."""

    client = SimpleNamespace(sqid="clt_test", pk=1, supports_pkce=False)
    state_token, record = oidc_state.issue(client, "https://app.example/callback")

    assert record.nonce != state_token
    assert oidc_state.consume(state_token) == record
    with pytest.raises(OidcFlowError) as exc_info:
        oidc_state.consume(state_token)

    assert exc_info.value.code == INVALID_STATE
    assert exc_info.value.http_status == 400


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


def _vendor_and_client(**overrides: Any) -> tuple[Vendor, Client]:
    """Create one enabled OIDC client for resolver tests."""

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
        client = Client.objects.create(vendor=vendor, **defaults)
    return vendor, client


def _stub_client(**overrides: Any) -> SimpleNamespace:
    """Return a client-like object for protocol-helper tests."""

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

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        """Return the key object shape PyJWT exposes."""

        del token
        return SimpleNamespace(key="secret")
