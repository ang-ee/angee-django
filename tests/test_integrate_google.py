"""The Google OAuth addon: one preset, one deployment client, login composing it."""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.fields import NOT_PROVIDED
from rebac import system_context

from angee.iam_integrate_oidc import identity
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.oidc import OidcProvider
from angee.integrate_google.providers import GoogleType
from angee.resources.entries import ResourceEntry, resource_manifest_for
from tests.conftest import OAuthClient

ROOT = Path(__file__).resolve().parents[1]
GOOGLE_ENDPOINT_HOSTS = ("accounts.google.com/o/oauth2", "oauth2.googleapis.com", "openidconnect.googleapis.com")


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


def test_google_seeds_name_the_preset_and_repeat_no_endpoint() -> None:
    rows = _seed_rows("integrate_google", "resources/install/010_integrate.oauthclient.yaml")

    assert set(rows) == {"oauth_google", "oauth_gemini"}
    google, gemini = rows["oauth_google"], rows["oauth_gemini"]
    assert (google["slug"], google["environment"], google["provider_type"]) == ("google", "prod", "google")
    assert "client_id" not in google and "client_secret" not in google
    assert gemini["provider_type"] == "google"
    assert gemini["client_id"] == "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    endpoint_fields = {
        "discovery_url", "issuer", "jwks_uri", "authorize_endpoint", "token_endpoint", "userinfo_endpoint",
        "revoke_endpoint", "authorize_params", "external_id_claim", "email_claim",
    }
    for row in (google, gemini):
        assert not endpoint_fields & row.keys()


def test_login_addon_sets_policy_on_the_google_rows_it_composes() -> None:
    rows = _seed_rows("iam_integrate_oidc", "resources/install/010_integrate.oauthclient.yaml")

    assert rows["oauth_google_oidc"] == {
        "_xref": "oauth_google_oidc",
        "slug": "google",
        "environment": "prod",
        "login_enabled": True,
        "link_on_email_match": True,
    }
    assert rows["oauth_gemini_oidc"] == {
        "_xref": "oauth_gemini_oidc", "slug": "gemini", "environment": "prod", "login_enabled": True,
    }


def test_google_endpoints_live_only_in_the_preset() -> None:
    sources = [path for pattern in ("*.py", "*.yaml") for path in (ROOT / "src" / "angee").rglob(pattern)]
    copies = {
        path.relative_to(ROOT).as_posix()
        for path in sources
        if any(host in path.read_text(encoding="utf-8") for host in GOOGLE_ENDPOINT_HOSTS)
    }

    assert copies == {"src/angee/integrate_google/providers.py"}


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
