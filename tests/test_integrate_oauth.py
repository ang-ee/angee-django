"""Addon provider refinements and protected-relation-free connection projections."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
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

from angee.impl import ImplClassField
from angee.integrate import connect
from angee.integrate.errors import IntegrationError, RequestNotSentError
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
def test_non_admin_connection_projection_batches_health_and_restricts_credential_status(monkeypatch, actor_kind):
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
    cipher = Credential._meta.get_field("material")._fernet()
    decrypt = Mock(wraps=cipher.decrypt)
    monkeypatch.setattr(cipher, "decrypt", decrypt)
    with actor_context(actor), CaptureQueriesContext(connection) as queries:
        result = schema.execute_sync(
            "{ feeds { credentialStatus isReconnectRequired isOauthConnectable } }", context_value=context,
        )
    assert result.errors is None
    assert result.data == {"feeds": [{
        "credentialStatus": "active" if actor_kind == "owner" else None,
        "isReconnectRequired": False if actor_kind == "owner" else None, "isOauthConnectable": True,
    }] * 4}
    feed_reads = [query["sql"] for query in queries if Feed._meta.db_table in query["sql"]]
    assert len(feed_reads) == 1
    assert "feed_backend_class" in feed_reads[0]
    assert "client_secret" not in " ".join(query["sql"] for query in queries)
    # Unexpired listing rows use only public health; no encrypted material is loaded.
    material_reads = [query for query in queries if "material" in query["sql"]]
    assert material_reads == []
    decrypt.assert_not_called()


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("rows", [1, 6])
@pytest.mark.parametrize("expired_row", [False, True])
def test_connection_health_listing_decrypts_only_the_expired_row(monkeypatch, rows, expired_row):
    with system_context(reason="test lazy health listing"):
        for index in range(rows):
            integration = make_integration(
                f"lazy-health-{index}", kind="oauth", material={"access_token": "fake", "refresh_token": "fake"},
            )
            if expired_row and index == 0:
                credential = integration.credential
                credential.expires_at = timezone.now() - timedelta(minutes=1)
                credential.save(update_fields=["expires_at"])
    field = Credential._meta.get_field("material")
    cipher = field._fernet()
    decrypt = Mock(wraps=cipher.decrypt)
    monkeypatch.setattr(cipher, "decrypt", decrypt)
    with CaptureQueriesContext(connection) as queries:
        credentials = list(Credential.objects.connection_health())
        assert all(not credential.is_reconnect_required for credential in credentials)
    assert len(credentials) == rows
    assert decrypt.call_count == int(expired_row)
    reads = [query for query in queries if query["sql"].startswith("SELECT")
             and Credential._meta.db_table in query["sql"]]
    assert len(reads) == 1 + int(expired_row)
    assert len([query for query in queries if "material" in query["sql"]]) == int(expired_row)


@pytest.mark.parametrize("status,failed,expired,refreshable,expected", [
    ("active", False, False, False, False), ("revoked", False, False, True, True),
    ("active", True, False, True, True), ("active", False, True, True, False),
    ("active", False, True, False, True),
])
def test_credential_health_derives_refreshability_from_owning_facts(status, failed, expired, refreshable, expected):
    credential = Credential(
        status=status, last_refresh_status="failed" if failed else "ok", kind="oauth",
        oauth_client=OAuthClient(supports_refresh=True),
        material=Credential.encode_material({
            "access_token": "fake", **({"refresh_token": "fake"} if refreshable else {}),
        }),
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
def test_material_and_client_changes_immediately_change_refreshability():
    with system_context(reason="test derived refreshability"):
        integration = make_integration("material-refreshability", kind="oauth", material={"access_token": "fake"})
        credential = integration.credential
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
        assert credential.is_reconnect_required
        credential.update_material(refresh_token="fake-refresh")
        assert credential.refreshable and not credential.is_reconnect_required
        credential.oauth_client.supports_refresh = False
        credential.oauth_client.save(update_fields=["supports_refresh"])
        credential.refresh_from_db()
        assert not credential.refreshable and credential.is_reconnect_required
        credential.oauth_client.supports_refresh = True
        credential.oauth_client.save(update_fields=["supports_refresh"])
        credential.update_material(refresh_token=None)
        assert not credential.refreshable and credential.is_reconnect_required


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("forced", [False, True])
@pytest.mark.parametrize("not_sent", [False, True])
def test_transport_failure_during_refresh_preserves_consent_health(monkeypatch, forced, not_sent):
    with system_context(reason="test transient refresh"):
        integration = make_integration(
            "temporary-refresh", kind="oauth", material={"access_token": "fake", "refresh_token": "fake-refresh"},
        )
        credential = integration.credential
        credential.oauth_client.token_endpoint = "https://provider.example/token"
        credential.oauth_client.client_secret = "fake-secret"
        credential.oauth_client.save(update_fields=["token_endpoint", "client_secret"])
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
    original = OAuthClientProtocol.__init__
    requests = []

    def refuse_transport(request):
        requests.append(request)
        if not_sent:
            raise RequestNotSentError()
        raise httpx2.ConnectError("private transport diagnostic", request=request)

    def with_transport(self, oauth_client):
        original(self, oauth_client)
        self._transport = httpx2.MockTransport(refuse_transport)

    monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
    with system_context(reason="test retryable refresh"), pytest.raises(OAuthFlowError) as raised:
        (credential.refresh_now if forced else credential.ensure_fresh)()
    credential.refresh_from_db()
    assert len(requests) == 1
    assert parse_qs(requests[0].content.decode())["grant_type"] == ["refresh_token"]
    assert raised.value.code == "token_exchange_failed" and raised.value.http_status == 503
    assert raised.value.transient and credential.last_refresh_status == "ok"
    assert isinstance(raised.value.__cause__, RequestNotSentError if not_sent else httpx2.ConnectError)
    assert credential.reveal() == {"access_token": "fake", "refresh_token": "fake-refresh"}
    assert credential.status == "active" and credential.refreshable
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



@pytest.mark.usefixtures("composed_tables")
def test_channel_typed_feed_resolves_the_feed_admission_selector():
    Channel = apps.get_model("messaging", "Channel")

    @strawberry_django.type(Channel)
    class ProjectedChannel(IntegrationConnectionMixin, IntegrationOAuthMixin):
        pass

    @strawberry.type
    class Query:
        @strawberry_django.field
        def channels(self, info: strawberry.Info) -> list[ProjectedChannel]:
            return Channel.objects.with_actor(info.context.request.user)

    schema = strawberry.Schema(query=Query, extensions=[RebacDjangoOptimizerExtension, OAuthRegistrationExtension])
    with system_context(reason="test parent-typed feed admission"):
        feed = make_integration("channel-feed-admission", model=Feed, backend_class="feed", feed_backend_class="stub")
        Feed.objects.filter(pk=feed.pk).update(credential=None, lifecycle="disconnected")
    with actor_context(feed.owner):
        result = schema.execute_sync(
            "{ channels { canConnect canResume canRetryDiscovery isOauthConnectable } }",
            context_value=SimpleNamespace(request=SimpleNamespace(user=feed.owner)),
        )
    assert result.errors is None
    assert result.data == {"channels": [{
        "canConnect": True, "canResume": False, "canRetryDiscovery": False, "isOauthConnectable": True,
    }]}


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("status", [429, 503, None])
@pytest.mark.parametrize("expired", [False, True])
def test_preexpiry_refresh_failure_keeps_valid_access_token(monkeypatch, status, expired):
    with system_context(reason="test preexpiry refresh"):
        credential = make_integration(
            "preexpiry-refresh", kind="oauth", material={"access_token": "fake", "refresh_token": "fake-refresh"},
        ).credential
        credential.expires_at = timezone.now() + timedelta(minutes=-1 if expired else 2)
        credential.save(update_fields=["expires_at"])

    def refuse(self, **kwargs):
        if status is None:
            raise RequestNotSentError()
        raise OAuthFlowError("token_exchange_failed", status)

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", refuse)
    with system_context(reason="test use existing access token"):
        if expired:
            with pytest.raises(IntegrationError):
                credential.ensure_fresh()
        else:
            credential.ensure_fresh()
            assert credential.secret_value() == "fake"
        credential.refresh_from_db()
        assert credential.last_refresh_status == "ok" and not credential.is_reconnect_required


def test_signing_secret_refuses_public_clients_without_reading_storage():
    class PublicClient:
        is_public_client = OAuthClient.is_public_client
        manual_redirect_uri = "https://provider.example/manual"

        @property
        def client_secret(self):
            raise AssertionError("public client secret storage must stay unread")

    with pytest.raises(IntegrationError, match="Public OAuth clients"):
        OAuthClientProtocol(PublicClient()).confidential_client_secret()
    protocol = OAuthClientProtocol(OAuthClient(manual_redirect_uri="", client_secret="fake"))
    assert protocol.confidential_client_secret() == "fake"


def test_signing_secret_refuses_a_confidential_client_without_a_secret():
    with pytest.raises(IntegrationError, match="has no client secret"):
        OAuthClientProtocol(OAuthClient(manual_redirect_uri="", client_secret="")).confidential_client_secret()
