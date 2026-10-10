"""Addon provider refinements and protected-relation-free connection projections."""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx2
import pytest
import strawberry
import strawberry_django
from django.apps import apps
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context, system_context
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension

from angee.base.impl import ImplClassField
from angee.integrate import connect
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol, with_query
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.oauth.providers import OAuthProviderType
from angee.integrate.oauth.registrations import OAuthRegistrationExtension, oauth_client_is_ready
from angee.integrate.schema import IntegrationConnectionMixin, IntegrationOAuthMixin
from tests.conftest import Credential, ExternalAccount, Feed, OAuthClient, create_user, make_integration


def test_query_parameters_replace_existing_keys_and_preserve_unrelated_values():
    result = with_query("https://provider.example/profile?proof=old&proof=older&lang=en#profile", {"proof": "new"})
    assert result == "https://provider.example/profile?lang=en&proof=new#profile"
    assert with_query("https://provider.example/profile?lang=en", {}) == "https://provider.example/profile?lang=en"


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("refused", [False, True])
def test_addon_refines_grant_through_authlib_before_identity_or_persistence(monkeypatch, refused):
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={
        "generic_oauth2": "tests.integrate_fixtures.RefiningProvider",
    }), system_context(reason="test addon grant refinement"):
        user = create_user("grant-user")
        client = OAuthClient.objects.create(
            slug="extension", provider_type="generic_oauth2", display_name="Extension", client_id="application",
            client_secret="fake", token_endpoint="https://provider.example/token",
            userinfo_endpoint="https://provider.example/profile", authorize_endpoint="https://provider.example/auth",
        )
        assert client._meta.get_field("provider_type").registered_keys() == ("generic_oauth2",)
        token, _record = state.issue(
            client, "https://app.example/callback", user_id=str(user.pk), flow=state.StateFlow.CONNECT,
        )
        requests = []

        def respond(request):
            if request.url.path == "/profile":
                assert request.headers["Authorization"] == "Bearer refined"
                assert request.url.params["proof"] == "refined"
                return httpx2.Response(200, json={"sub": "external-user"})
            grant = parse_qs(request.content.decode())
            requests.append(grant)
            if grant["grant_type"] == ["resource_grant"]:
                if refused:
                    return httpx2.Response(400, json={"error": "invalid_grant"})
                return httpx2.Response(200, json={"access_token": "refined", "expires_in": 7200, "private": "omit"})
            return httpx2.Response(200, json={"access_token": "short", "expires_in": 60})

        original = OAuthClientProtocol.__init__

        def with_transport(self, oauth_client):
            original(self, oauth_client)
            self._transport = httpx2.MockTransport(respond)

        monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
        if refused:
            with pytest.raises(OAuthFlowError):
                connect.complete_account_connect(
                    client, code="code", state_token=token, redirect_uri="https://app.example/callback",
                )
            assert Credential.objects.count() == ExternalAccount.objects.count() == 0
        else:
            result = connect.complete_account_connect(
                client, code="code", state_token=token, redirect_uri="https://app.example/callback",
            )
            assert result.account.external_id == "external-user"
            assert result.credential.reveal() == {"access_token": "refined", "expires_in": 7200}
        assert requests[1] == {
            "grant_type": ["resource_grant"], "short_token": ["short"],
            "client_id": ["application"], "client_secret": ["fake"],
        }


@pytest.mark.usefixtures("composed_tables")
def test_registration_snapshot_uses_readiness_and_environment_priority_without_secret_columns():
    with system_context(reason="test registration readiness"):
        OAuthClient.objects.create(slug="preferred", environment="dev", display_name="Development", client_id="dev")
        production = OAuthClient.objects.create(
            slug="preferred", display_name="Production", client_id="prod", is_enabled=False,
            authorize_endpoint="https://provider.example/auth", token_endpoint="https://provider.example/token",
        )
        OAuthClient.objects.create(slug="incomplete", display_name="Incomplete", client_id="application")
    operation = OAuthRegistrationExtension().on_operation()
    next(operation)
    try:
        with CaptureQueriesContext(connection) as queries:
            assert not oauth_client_is_ready("preferred")
            assert not oauth_client_is_ready("incomplete")
            assert not oauth_client_is_ready("missing")
    finally:
        operation.close()
    reads = [query["sql"] for query in queries if query["sql"].startswith("SELECT")]
    assert len(reads) == 1, reads
    assert "client_secret" not in reads[0]
    with system_context(reason="test configure registration"):
        production.is_enabled = True
        production.save(update_fields=["is_enabled"])
        assert list(OAuthClient.objects.connectable()) == [production]
        assert OAuthClient.objects.enabled_for_slug("preferred") == production
    assert oauth_client_is_ready("preferred")


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("actor_kind", ["owner", "reader"])
def test_non_admin_connection_projection_uses_rebac_optimizer_and_elevated_public_scalars(actor_kind):
    @strawberry_django.type(Feed)
    class ProjectedFeed(IntegrationConnectionMixin, IntegrationOAuthMixin):
        pass

    @strawberry.type
    class Query:
        @strawberry_django.field
        def feeds(self, info: strawberry.Info) -> list[ProjectedFeed]:
            return Feed.objects.with_actor(info.context.request.user)

    schema = strawberry.Schema(query=Query, extensions=[RebacDjangoOptimizerExtension, OAuthRegistrationExtension])
    with system_context(reason="test projection permissions"):
        owner, reader = create_user("projection-owner"), create_user("projection-reader")
        for index in range(4):
            feed = make_integration(
                f"projection-{index}", model=Feed, owner=owner, backend_class="feed", feed_backend_class="stub",
            )
            client = feed.credential.oauth_client
            client.authorize_endpoint = "https://provider.example/auth"
            client.token_endpoint = "https://provider.example/token"
            client.save(update_fields=["authorize_endpoint", "token_endpoint"])
            apps.get_model("messaging", "Channel").objects.get(pk=feed.pk).grant_record_access("reader", reader)
        assert Feed.capability_impl_field().name == "feed_backend_class"
    actor = owner if actor_kind == "owner" else reader
    context = SimpleNamespace(request=SimpleNamespace(user=actor))
    with actor_context(actor), CaptureQueriesContext(connection) as queries:
        result = schema.execute_sync(
            "{ feeds { credentialStatus isReconnectRequired isOauthConnectable } }", context_value=context,
        )
    assert result.errors is None
    assert result.data == {"feeds": [{
        "credentialStatus": "active", "isReconnectRequired": False, "isOauthConnectable": True,
    }] * 4}
    feed_reads = [query["sql"] for query in queries if Feed._meta.db_table in query["sql"]]
    assert len(feed_reads) == 1
    assert "feed_backend_class" in feed_reads[0]
    assert "client_secret" not in " ".join(query["sql"] for query in queries)
    assert "material" not in " ".join(query["sql"] for query in queries)


@pytest.mark.parametrize("status,failed,expired,refreshable,expected", [
    ("active", False, False, False, False), ("revoked", False, False, True, True),
    ("active", True, False, True, True), ("active", False, True, True, False),
    ("active", False, True, False, True),
])
def test_credential_health_never_opens_material(status, failed, expired, refreshable, expected):
    credential = Credential(
        status=status, last_refresh_status="failed" if failed else "ok", refreshable=refreshable,
        expires_at=timezone.now() - timedelta(minutes=1) if expired else None,
    )
    assert credential.is_reconnect_required is expected


@pytest.mark.usefixtures("composed_tables")
def test_expired_refreshable_grant_is_reused_but_failed_refresh_requires_consent():
    with system_context(reason="test consent predicate"):
        integration = make_integration(
            "refreshable-grant", kind="oauth", material={"access_token": "fake", "refresh_token": "fake-refresh"},
        )
        credential = integration.credential
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
        assert Credential.objects.live_oauth_for_user(credential.user, credential.oauth_client).pk == credential.pk
        credential.last_refresh_status = "failed"
        credential.save(update_fields=["last_refresh_status"])
        assert Credential.objects.live_oauth_for_user(credential.user, credential.oauth_client) is None


@pytest.mark.usefixtures("composed_tables")
def test_material_owner_updates_the_public_refreshability_fact():
    with system_context(reason="test stored refreshability"):
        integration = make_integration("material-refreshability", kind="oauth", material={"access_token": "fake"})
        credential = integration.credential
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
        assert credential.is_reconnect_required
        credential.update_material(refresh_token="fake-refresh")
        assert credential.refreshable and not credential.is_reconnect_required
        credential.update_material(refresh_token=None)
        assert not credential.refreshable and credential.is_reconnect_required


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("forced", [False, True])
def test_transport_failure_during_refresh_preserves_consent_health(monkeypatch, forced):
    with system_context(reason="test transient refresh"):
        integration = make_integration(
            "temporary-refresh", kind="oauth", material={"access_token": "fake", "refresh_token": "fake-refresh"},
        )
        credential = integration.credential
        credential.oauth_client.token_endpoint = "https://provider.example/token"
        credential.oauth_client.save(update_fields=["token_endpoint"])
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
    original = OAuthClientProtocol.__init__

    def refuse_transport(request):
        raise httpx2.ConnectError("private transport diagnostic", request=request)

    def with_transport(self, oauth_client):
        original(self, oauth_client)
        self._transport = httpx2.MockTransport(refuse_transport)

    monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
    with system_context(reason="test retryable refresh"), pytest.raises(OAuthFlowError) as raised:
        (credential.refresh_now if forced else credential.ensure_fresh)()
    credential.refresh_from_db()
    assert raised.value.transient and credential.last_refresh_status == "ok"
    assert not credential.is_reconnect_required


def test_provider_choices_are_contributed_through_the_native_impl_registry():
    with override_settings(ANGEE_OAUTH_PROVIDER_TYPE_CLASSES={
        "generic_oauth2": "angee.integrate.oauth.providers.GenericOAuth2",
        "contributed": "tests.integrate_fixtures.ContributedProvider",
    }):
        field = ImplClassField(OAuthProviderType)
        assert field.registered_keys() == ("contributed", "generic_oauth2")
        assert {choice.key for choice in field.impl_choices()} == {"contributed", "generic_oauth2"}
        assert field.resolve_class("contributed").key == "contributed"
