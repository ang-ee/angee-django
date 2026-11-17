"""The Google OAuth addon: one preset, one deployment client, login composing it."""

from __future__ import annotations

from types import SimpleNamespace

import httpx2
import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models.fields import NOT_PROVIDED
from rebac import system_context

from angee.iam_integrate_oidc import identity
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.oidc import OidcProvider
from angee.integrate_google.compose_migrations.integrate.align_google_clients import (
    PRESET_ENDPOINT_FIELDS,
    align_google_clients,
)
from angee.integrate_google.compose_migrations.resources.rehome_google_oauth_resources import rehome
from angee.integrate_google.providers import GoogleType
from angee.resources.entries import ResourceEntry, resource_manifest_for
from angee.resources.testing.models import Resource
from tests.conftest import OAuthClient


def _seed_rows(app_label: str, path: str) -> dict[str, dict]:
    config = apps.get_app_config(app_label)
    declaration = next(item for item in resource_manifest_for(config)["install"] if item["path"] == path)
    rows = ResourceEntry.from_declaration(config, "install", declaration).read_groups()[0].dataset.dict
    # One dataset carries every column any row names; a row's own values are the declared ones.
    return {row["_xref"]: {key: value for key, value in row.items() if value is not NOT_PROVIDED} for row in rows}


def test_google_preset_owns_endpoints_metadata_scopes_and_no_login_policy() -> None:
    defaults = GoogleType.effective_defaults()

    assert defaults["issuer"] == "https://accounts.google.com"
    assert defaults["jwks_uri"] == "https://www.googleapis.com/oauth2/v3/certs"
    assert defaults["authorize_params"] == {"access_type": "offline", "include_granted_scopes": "true"}
    assert defaults["default_scopes"] == ["openid", "email", "profile"]
    assert {
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/cloud-platform",
    } <= set(defaults["scopes_catalogue"])
    assert not {"login_enabled", "link_on_email_match", "create_on_login"} & defaults.keys()


@pytest.mark.django_db
def test_google_client_takes_the_preset_profile_endpoint_on_create() -> None:
    with system_context(reason="test Google preset identity"):
        client = OAuthClient.objects.create(slug="google-profile", provider_type="google")
    endpoint = GoogleType.effective_defaults()["userinfo_endpoint"]
    assert endpoint == "https://openidconnect.googleapis.com/v1/userinfo"
    assert client.userinfo_endpoint == endpoint
    assert client.issuer == "https://accounts.google.com"
    assert client.login_enabled is False

    with mock_transport(lambda request: httpx2.Response(200, json={"sub": "provider-subject"})) as requests:
        claims = OAuthClientProtocol(client).fetch_userinfo("fake")
    assert [str(request.url) for request in requests] == [endpoint]
    assert client.external_id_from_claims(claims) == "provider-subject"


def test_google_id_tokens_verify_with_either_issuer_form_and_the_connect_prompt_is_googles() -> None:
    client = OAuthClient(
        provider_type="google", client_id="google-client", issuer="https://accounts.google.com",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    )
    provider = OidcProvider(issuer="https://accounts.google.com", client_id="google-client")
    protocol = OAuthClientProtocol(client)
    with mock_transport(provider):
        for issuer in ("https://accounts.google.com", "accounts.google.com"):
            assert protocol.verify_id_token(provider.id_token(sub="google-subject", iss=issuer))["iss"] == issuer
        with pytest.raises(OAuthFlowError):
            protocol.verify_id_token(provider.id_token(sub="google-subject", iss="https://elsewhere.example"))

    assert GoogleType.authorize_params(state.StateFlow.CONNECT, {}, consent=False) == {"prompt": "select_account"}
    assert GoogleType.authorize_params(state.StateFlow.LOGIN, {}, consent=False) == {}


def test_google_seeds_the_deployment_client_bare_and_keeps_the_released_gemini_row() -> None:
    rows = _seed_rows("integrate_google", "resources/install/010_integrate.oauthclient.yaml")

    assert rows["oauth_google"] == {
        "_xref": "oauth_google", "slug": "google", "environment": "prod", "provider_type": "google",
        "display_name": "Google", "icon": "google",
    }
    # Gemini keeps the content integrate released, so the rehomed ledger skips it.
    gemini = rows["oauth_gemini"]
    assert gemini["client_id"] == "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    assert "provider_type" not in gemini and gemini["userinfo_endpoint"] == ""


@pytest.mark.django_db
def test_the_seeded_google_client_takes_its_endpoints_from_the_preset() -> None:
    with system_context(reason="test Google seed"):
        Resource.objects.load_addons((apps.get_app_config("integrate_google"),), tiers=[Resource.Tier.INSTALL])
        google = OAuthClient.objects.get(slug="google", environment="prod")

    preset = GoogleType.effective_defaults()
    for field in PRESET_ENDPOINT_FIELDS:
        assert getattr(google, field) == preset[field]
    assert google.scopes_catalogue == preset["scopes_catalogue"]
    assert (google.client_id, google.configuration_state) == ("", "needs_client")


@pytest.mark.django_db
def test_align_step_fills_the_preset_on_existing_google_clients_and_keeps_operator_values() -> None:
    with system_context(reason="test Google client upgrade"):
        demo = OAuthClient.objects.create(
            slug="google", provider_type="google", display_name="Google (demo)", client_id="demo-oidc-client",
        )
        gemini = OAuthClient.objects.create(slug="gemini", provider_type="google", display_name="Gemini")
        # Rows created before the preset carried these columns.
        OAuthClient.objects.filter(pk=demo.pk).update(
            revoke_endpoint="", issuer="", jwks_uri="", scopes_catalogue=["openid", "custom"],
            authorize_params={"access_type": "online"},
        )
        OAuthClient.objects.filter(pk=gemini.pk).update(scopes_catalogue=["cloud-platform"])

        align_google_clients(apps, SimpleNamespace(connection=connection))

        demo.refresh_from_db()
        gemini.refresh_from_db()
    preset = GoogleType.effective_defaults()
    assert (demo.revoke_endpoint, demo.issuer, demo.jwks_uri) == (
        preset["revoke_endpoint"], preset["issuer"], preset["jwks_uri"],
    )
    assert demo.scopes_catalogue[:2] == ["openid", "custom"]
    assert set(preset["scopes_catalogue"]) <= set(demo.scopes_catalogue)
    assert demo.authorize_params == {"access_type": "online", "include_granted_scopes": "true"}
    assert demo.client_id == "demo-oidc-client"
    assert gemini.scopes_catalogue == ["cloud-platform"]


@pytest.mark.django_db
def test_gemini_ledger_rehome_preserves_identity_and_refuses_a_competing_ledger() -> None:
    values = dict(
        source_path="resources/install/010_integrate.oauthclient.yaml", tier="install", xref="oauth_gemini",
        content_hash="retained-hash", target_model="integrate.OAuthClient", target_id="clt_existing",
    )
    with system_context(reason="test Gemini ledger rehome"):
        row = Resource.objects.create(source_addon="angee.integrate", **values)
        rehome(apps, SimpleNamespace(connection=connection))
        row.refresh_from_db()
        assert (row.source_addon, row.content_hash) == ("angee.integrate_google", "retained-hash")

        Resource.objects.create(source_addon="angee.integrate", **values)
        with pytest.raises(RuntimeError, match="Both Integrate and integrate_google"), transaction.atomic():
            rehome(apps, SimpleNamespace(connection=connection))


@pytest.mark.django_db
def test_login_through_the_google_preset_links_the_verified_email() -> None:
    user = get_user_model().objects.create_user(username="google-login", email="person@example.com")
    with system_context(reason="test Google login row"):
        client = OAuthClient.objects.create(
            slug="google", provider_type="google", display_name="Google", client_id="google-client",
            client_secret="fake", login_enabled=True, link_on_email_match=True,
        )
    redirect = "https://app.example/sso/callback"
    token, record = state.issue(client, redirect, next_path="/home")
    provider = OidcProvider(
        issuer="https://accounts.google.com",
        client_id="google-client",
        id_claims={
            "sub": "google-subject", "email": "person@example.com", "email_verified": True, "nonce": record.nonce,
        },
    )

    with mock_transport(provider) as requests:
        completion = identity.complete_login(client, code="code", state_token=token, redirect_uri=redirect)

    assert completion.user.pk == user.pk
    assert completion.next_path == "/home"
    assert {str(request.url) for request in requests} >= {
        "https://oauth2.googleapis.com/token", "https://www.googleapis.com/oauth2/v3/certs",
    }
