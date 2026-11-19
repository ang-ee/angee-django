"""The Google OAuth addon: one preset, one deployment client, login composing it."""

from __future__ import annotations

from collections.abc import Collection
from datetime import timedelta
from types import ModuleType

import httpx2
import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.state import ProjectState
from django.db.models.fields import NOT_PROVIDED
from rebac import system_context

from angee.iam_integrate_oidc import identity
from angee.integrate.errors import RequestNotSentError
from angee.integrate.http import JSON_MAX_BYTES, HttpRefusal
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.oidc import OidcProvider
from angee.integrate_google.api import GoogleApi, GoogleApiError
from angee.integrate_google.compose_migrations.integrate import align_google_clients as align_template
from angee.integrate_google.compose_migrations.resources import rehome_gemini_seed as rehome_template
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


def _apply(template: ModuleType, app_label: str, *, reverse: bool = False) -> None:
    """Run a migration template's ``Migration`` over the installed models' migration state.

    The callables get historical models (no sqid, managers or methods), as a host's copy does.
    """

    migration = template.Migration(template.__name__.rpartition(".")[2], app_label)
    with connection.schema_editor() as editor:
        state = ProjectState.from_apps(apps)
        if reverse:
            migration.unapply(state, editor)
        else:
            migration.apply(state, editor)


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
    for field in align_template.PRESET_ENDPOINT_FIELDS:
        assert getattr(google, field) == preset[field]
    assert google.scopes_catalogue == preset["scopes_catalogue"]
    assert (google.client_id, google.configuration_state) == ("", "needs_client")


@pytest.mark.django_db
@pytest.mark.parametrize("existing", [False, True])
def test_seeds_turn_google_sign_in_on_only_for_a_google_client_they_create(existing: bool) -> None:
    with system_context(reason="test Google sign-in seed"):
        if existing:
            OAuthClient.objects.create(slug="google", provider_type="google", display_name="Operator")
        addons = [apps.get_app_config(label) for label in ("integrate", "integrate_google", "iam_integrate_oidc")]
        Resource.objects.load_addons(addons, tiers=[Resource.Tier.INSTALL])
        google = OAuthClient.objects.get(slug="google", environment="prod")

    assert (google.login_enabled, google.link_on_email_match) == (not existing, not existing)


@pytest.mark.django_db
@pytest.mark.parametrize("existing", [False, True])
def test_seeds_never_decide_sign_in_through_the_public_gemini_client(existing: bool) -> None:
    """A fresh install offers no single sign-on; an operator's Gemini switch survives a load."""

    with system_context(reason="test Gemini sign-in seed"):
        if existing:
            OAuthClient.objects.create(slug="gemini", provider_type="google", display_name="Gemini", login_enabled=True)
        addons = [apps.get_app_config(label) for label in ("integrate", "integrate_google", "iam_integrate_oidc")]
        Resource.objects.load_addons(addons, tiers=[Resource.Tier.INSTALL])
        gemini = OAuthClient.objects.get(slug="gemini", environment="prod")
        offered = set(OAuthClient.login_picker_queryset(OAuthClient.objects.all()).values_list("slug", flat=True))

    # Google's published client id makes the row startable, so only its login policy keeps it out.
    assert gemini.is_enabled and gemini.client_id and gemini.issuer
    assert gemini.login_enabled is existing
    assert offered == ({"gemini"} if existing else set())


@pytest.mark.django_db(transaction=True)
def test_align_template_fills_the_preset_on_existing_google_clients_and_keeps_operator_values() -> None:
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

        _apply(align_template, "integrate")
        demo.refresh_from_db()
        aligned = OAuthClient.objects.values().get(pk=demo.pk)
        # A second run changes nothing, and the reverse leaves the filled values.
        _apply(align_template, "integrate")
        _apply(align_template, "integrate", reverse=True)
        assert OAuthClient.objects.values().get(pk=demo.pk) == aligned
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


@pytest.mark.django_db(transaction=True)
def test_gemini_rehome_template_preserves_identity_reverses_and_refuses_a_competing_ledger() -> None:
    values = dict(
        source_path="resources/install/010_integrate.oauthclient.yaml", tier="install", xref="oauth_gemini",
        content_hash="retained-hash", target_model="integrate.OAuthClient", target_id="clt_existing",
    )
    with system_context(reason="test Gemini ledger rehome"):
        row = Resource.objects.create(source_addon="angee.integrate", **values)
        _apply(rehome_template, "resources")
        row.refresh_from_db()
        assert (row.source_addon, row.content_hash) == ("angee.integrate_google", "retained-hash")
        _apply(rehome_template, "resources", reverse=True)
        row.refresh_from_db()
        assert (row.source_addon, row.content_hash) == ("angee.integrate", "retained-hash")
        _apply(rehome_template, "resources")

        Resource.objects.create(source_addon="angee.integrate", **values)
        with pytest.raises(RuntimeError, match="Both angee.integrate and angee.integrate_google"):
            _apply(rehome_template, "resources")
        row.refresh_from_db()
        assert row.source_addon == "angee.integrate_google"


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


class _MintingKey:
    """A credential that mints per scope set and cannot refresh, as a ``jwt_bearer`` key behaves."""

    refreshable = False

    def __init__(self) -> None:
        self.scopes: tuple[str, ...] = ()

    def ensure_fresh(self) -> None:
        return None

    def auth_headers(self, *, scopes: Collection[str] = ()) -> dict[str, str]:
        self.scopes = tuple(scopes)
        return {"Authorization": "Bearer minted"}


def test_the_google_transport_names_account_refusals_and_asks_for_the_clients_scopes() -> None:
    answers = iter(
        [
            httpx2.Response(
                403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, headers={"Retry-After": "30"}
            ),
            httpx2.Response(403, json={"error": {"status": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}}),
            httpx2.Response(503),
            httpx2.Response(401),
            httpx2.Response(400, json={"error": {"errors": [{"reason": "badRequest"}]}}),
        ]
    )
    key = _MintingKey()
    with mock_transport(lambda request: next(answers)) as requests:
        api = GoogleApi(key, service="Google Calendar", grant="Calendar", scopes=("calendar.events",))
        url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
        refusals = [api.account_error(HttpRefusal.of(api.send("GET", url))) for _answer in range(4)]
        own = api.send("GET", url)

    rate, grant, outage, rejected = refusals
    assert key.scopes == ("calendar.events",)
    assert all(request.headers["authorization"] == "Bearer minted" for request in requests)
    assert isinstance(rate, GoogleApiError) and rate.transient and rate.retry_after == timedelta(seconds=30)
    assert grant is not None and "has not granted Calendar access" in str(grant) and not grant.transient
    assert outage is not None and outage.transient
    # A key that cannot refresh is not retried: the 401 asks for a reconnect at once.
    assert rejected is not None and "reconnect the Google account" in str(rejected) and len(requests) == 5
    assert api.account_error(HttpRefusal.of(own)) is None
    with pytest.raises(GoogleApiError, match="Google Calendar refused the request"):
        api.raise_for_status(own)


@pytest.mark.parametrize("headers", [{}, {"Retry-After": "30"}], ids=["no-wait", "its-retry-after"])
def test_a_rate_limit_google_answers_with_a_403_is_transient_through_the_hand_off(headers: dict[str, str]) -> None:
    """Google names its rate limit in a 403's reason: the hand-off states it transient, the answer's wait kept."""

    api = GoogleApi(_MintingKey(), service="Google Calendar", grant="Calendar")
    answer = httpx2.Response(403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}, headers=headers)
    rate = api.account_error(HttpRefusal.of(answer))

    assert rate is not None and rate.transient
    assert rate.retry_after == (timedelta(seconds=30) if headers else None)
    assert str(rate) == "Google Calendar is rate limiting this account; try the sync again later."


@pytest.mark.parametrize(("answer", "transient", "wait", "message"), [
    pytest.param(
        httpx2.Response(200, content=b" " * (JSON_MAX_BYTES + 1)), False, None,
        "Google Calendar returned an oversized response.", id="an-oversized-answer-is-permanent",
    ),
    pytest.param(
        httpx2.Response(200, text="not json"), False, None,
        "Google Calendar returned an unreadable response.", id="an-unreadable-answer-is-permanent",
    ),
    pytest.param(
        httpx2.Response(200, json=["not", "an", "object"]), False, None,
        "Google Calendar returned an unreadable response.", id="an-answer-that-is-not-an-object-is-permanent",
    ),
    pytest.param(
        RequestNotSentError(), True, None, "Google Calendar could not be reached.",
        id="a-request-not-sent-is-the-apis-transient-refusal",
    ),
    pytest.param(
        httpx2.Response(503, headers={"Retry-After": "30"}), True, timedelta(seconds=30),
        "Google Calendar is temporarily unavailable (HTTP 503).", id="an-outage-waits-for-its-retry-after",
    ),
    pytest.param(
        httpx2.Response(400), False, None, "Google Calendar refused the request (HTTP 400).",
        id="the-requests-own-refusal-names-its-status",
    ),
])
def test_the_google_transport_classifies_each_refusal_by_the_shared_rule(
    answer: httpx2.Response | Exception, transient: bool, wait: timedelta | None, message: str,
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    api = GoogleApi(_MintingKey(), service="Google Calendar", grant="Calendar")
    with mock_transport(respond), pytest.raises(GoogleApiError) as refused:
        response = api.send("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList")
        api.raise_for_status(response)
        api.json(response)

    assert (refused.value.transient, refused.value.retry_after, str(refused.value)) == (transient, wait, message)
