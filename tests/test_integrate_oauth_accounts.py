"""One OAuth client, several accounts per user, per-consumer scopes and ID-token identity."""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection, transaction
from rebac import actor_context, system_context

from angee.graphql.testing.documents import graphql_request
from angee.integrate import connect
from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth import flow, state
from angee.integrate.oauth.errors import (
    ACCOUNT_SWITCH_REFUSED,
    INVALID_ID_TOKEN,
    SCOPE_NOT_GRANTED,
    SCOPE_NOT_IN_CATALOGUE,
    ConsentRequired,
    OAuthFlowError,
)
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.integration import Integration
from angee.integrate.testing.oidc import OidcProvider
from angee.jobs.celery import app as celery_app
from tests.conftest import (
    Credential,
    ExternalAccount,
    OAuthClient,
    create_platform_admin,
    create_user,
    make_integration,
)
from tests.integrate_fixtures import ConnectionAdapter, ConnectionBridge

pytestmark = pytest.mark.django_db

REDIRECT = "https://app.example/integrate/oauth/callback"
ISSUER = "https://provider.example"


def _bridge(slug: str, **client_fields: Any) -> tuple[ConnectionBridge, OAuthClient]:
    """Create an owned, unbound bridge and an OAuth client that answers the fake OpenID provider."""

    bridge = make_integration(slug, kind=CredentialKind.OAUTH, model=ConnectionBridge)
    fields = {
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "issuer": ISSUER,
        "jwks_uri": f"{ISSUER}/jwks",
        "default_scopes": ["openid", "email"],
        "scopes_catalogue": ["openid", "email", "calendar", "mail"],
        **client_fields,
    }
    with system_context(reason="test oauth accounts client"):
        client = OAuthClient.objects.get(pk=bridge.credential.oauth_client_id)
        for name, value in fields.items():
            setattr(client, name, value)
        client.client_secret = "fake"
        client.save()
        fixture_credential = bridge.credential_id
        ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=None, account=None, lifecycle="disconnected")
        Credential.objects.filter(pk=fixture_credential).delete()
    bridge.refresh_from_db()
    return bridge, client


def _connect(
    client: OAuthClient, user: Any, sub: str, *, refresh: str | None = "refresh",
    scope: str = "openid email calendar", state_token: str | None = None,
) -> Any:
    """Complete one account connect for ``sub`` through the fake provider."""

    token = {"access_token": f"access-{sub}", "expires_in": 3600, "scope": scope}
    if refresh is not None:
        token["refresh_token"] = f"{refresh}-{sub}"
    provider = OidcProvider(
        issuer=ISSUER, client_id=client.client_id, token=token, userinfo={"sub": sub, "email": f"{sub}@example.com"},
    )
    if state_token is None:
        state_token, _record = state.issue(client, REDIRECT, user_id=str(user.pk), flow=state.StateFlow.CONNECT)
    with mock_transport(provider):
        return connect.complete_account_connect(client, code="code", state_token=state_token, redirect_uri=REDIRECT)


def _authorize_params(started: Any) -> dict[str, list[str]]:
    return parse_qs(urlsplit(started.authorize_url).query)


def _start(bridge: ConnectionBridge, client: OAuthClient) -> Any:
    return connect.connect_integration_target(
        bridge, client, user=bridge.owner, request=graphql_request(bridge.owner, session=True),
        redirect_uri=REDIRECT, next_path="/",
    )


def test_one_user_connects_two_accounts_through_one_client_and_both_stay_live() -> None:
    bridge, client = _bridge("two-accounts")
    user = bridge.owner

    first = _connect(client, user, "account-one")
    second = _connect(client, user, "account-two")

    assert first.credential.pk != second.credential.pk
    for completion in (first, second):
        live = Credential.objects.live_oauth_for_user(user, client, external_account=completion.account)
        assert live is not None and live.pk == completion.credential.pk
        assert live.reveal()["refresh_token"] == f"refresh-{completion.account.external_id}"


def test_reconnecting_one_account_never_touches_another() -> None:
    bridge, client = _bridge("reconnect-isolation")
    user = bridge.owner
    first = _connect(client, user, "account-one")
    second = _connect(client, user, "account-two")
    bridge.attach_credential(second.credential)
    bridge.refresh_from_db()
    with system_context(reason="test reconnect isolation snapshot"):
        untouched = Credential.objects.get(pk=second.credential.pk)
    generation = bridge.discovery_generation

    # A re-consent that returns no refresh token keeps the stored one.
    again = _connect(client, user, "account-one", refresh=None)

    assert again.credential.pk == first.credential.pk
    assert again.credential.reveal()["access_token"] == "access-account-one"
    assert again.credential.reveal()["refresh_token"] == "refresh-account-one"
    with system_context(reason="test reconnect isolation assertion"):
        after = Credential.objects.get(pk=second.credential.pk)
    assert (after.material, after.updated_at) == (untouched.material, untouched.updated_at)
    bridge.refresh_from_db()
    assert (bridge.credential_id, bridge.discovery_generation) == (second.credential.pk, generation)


def test_a_credential_stored_without_an_account_is_adopted_by_the_first_account() -> None:
    bridge, client = _bridge("legacy-adoption")
    user = bridge.owner
    with system_context(reason="test legacy credential"):
        legacy = Credential.objects.upsert_for_user(user, client, CredentialKind.OAUTH, {"access_token": "legacy"})
        ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=legacy)

    adopted = _connect(client, user, "account-one")

    assert adopted.credential.pk == legacy.pk
    assert adopted.credential.external_account_id == adopted.account.pk
    bridge.refresh_from_db()
    assert bridge.account_id == adopted.account.pk


def test_connecting_another_account_into_a_bound_integration_is_refused() -> None:
    bridge, client = _bridge("account-switch")
    user = bridge.owner
    first = _connect(client, user, "account-one")
    bridge.attach_credential(first.credential)
    with system_context(reason="test account switch needs consent"):
        Credential.objects.filter(pk=first.credential.pk).update(last_refresh_status="failed")
    bridge.refresh_from_db()

    started = _start(bridge, client)
    params = _authorize_params(started)
    assert params["login_hint"] == ["account-one@example.com"]
    assert "prompt" not in params  # The generic preset asks for no prompt.

    switched = _connect(client, user, "account-two", state_token=started.state)
    with actor_context(user), pytest.raises(OAuthFlowError) as refused:
        Integration.objects.attach_completed_connect(
            switched.integration_id, user, switched.credential, exposed_model_labels={ConnectionBridge._meta.label},
        )

    assert refused.value.code == ACCOUNT_SWITCH_REFUSED
    bridge.refresh_from_db()
    assert (bridge.credential_id, bridge.account_id) == (first.credential.pk, first.account.pk)


def test_the_binding_survives_detach_and_follows_the_identity_across_clients_of_one_issuer() -> None:
    bridge, client = _bridge("binding")
    user = bridge.owner
    first = _connect(client, user, "account-one")
    bridge.attach_credential(first.credential)
    bridge.attach_credential(None)
    bridge.refresh_from_db()
    assert (bridge.credential_id, bridge.account_id) == (None, first.account.pk)

    with system_context(reason="test second client of one issuer"):
        other_client = OAuthClient.objects.create(
            slug="binding-second", display_name="Second", client_id="second-client", client_secret="fake",
            authorize_endpoint=f"{ISSUER}/authorize", token_endpoint=f"{ISSUER}/token",
            userinfo_endpoint=f"{ISSUER}/userinfo", issuer=ISSUER, jwks_uri=f"{ISSUER}/jwks",
        )
    moved = _connect(other_client, user, "account-one")
    bridge.attach_credential(moved.credential)
    bridge.refresh_from_db()
    assert bridge.account_id == moved.account.pk

    other = _connect(other_client, user, "account-two")
    with pytest.raises(OAuthFlowError) as refused:
        bridge.attach_credential(other.credential)
    assert refused.value.code == ACCOUNT_SWITCH_REFUSED


def test_ownership_transfer_hands_the_recipient_an_unbound_integration() -> None:
    bridge, client = _bridge("binding-transfer")
    first = _connect(client, bridge.owner, "account-one")
    bridge.attach_credential(first.credential)
    admin = create_platform_admin("binding-transfer-admin")
    recipient = create_user("binding-transfer-recipient")

    bridge.with_actor(admin).transfer_ownership(recipient)

    bridge.refresh_from_db()
    # The integration no longer runs on the previous owner's grant, nor is bound to their account.
    assert (bridge.owner_id, bridge.account_id, bridge.credential_id) == (recipient.pk, None, None)
    assert bridge.lifecycle == "disconnected"
    with actor_context(recipient):
        assert bridge.can_connect(recipient)


def test_the_binding_cannot_be_patched_away_or_switched(monkeypatch) -> None:
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    bridge, client = _bridge("binding-patch")
    first = _connect(client, bridge.owner, "account-one")
    second = _connect(client, bridge.owner, "account-two")
    bridge.attach_credential(first.credential)
    bridge.refresh_from_db()

    def patch(fields: dict[str, Any]) -> None:
        with actor_context(bridge.owner), transaction.atomic():
            bridge.with_actor(bridge.owner).prepare_patch(fields)

    # A null account keeps the binding, so another account's credential is still refused.
    with pytest.raises(OAuthFlowError) as refused:
        patch({"credential_id": second.credential.pk, "account_id": None})
    assert refused.value.code == ACCOUNT_SWITCH_REFUSED
    with pytest.raises(OAuthFlowError) as switched:
        patch({"account_id": second.account.pk})
    assert switched.value.code == ACCOUNT_SWITCH_REFUSED
    patch({"credential_id": None, "account_id": None})
    bridge.refresh_from_db()
    assert (bridge.credential_id, bridge.account_id) == (None, first.account.pk)


def test_consumer_scopes_join_the_defaults_and_must_be_in_the_catalogue() -> None:
    client = OAuthClient(slug="scoped", default_scopes=["openid", "email"], scopes_catalogue=["calendar", "mail"])

    assert client.requested_scopes() == ["openid", "email"]
    assert client.requested_scopes(["calendar", "email", "calendar"]) == ["openid", "email", "calendar"]
    assert client.requested_scopes(["mail"], granted=["calendar", "userinfo.unknown"]) == [
        "openid", "email", "mail", "calendar",
    ]
    with pytest.raises(OAuthFlowError) as refused:
        client.requested_scopes(["drive"])
    assert refused.value.code == SCOPE_NOT_IN_CATALOGUE


def test_connect_requests_the_consumer_scopes_and_leaves_the_prompt_to_the_provider(monkeypatch) -> None:
    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))
    bridge, client = _bridge("scoped-connect", authorize_params={"access_type": "offline", "prompt": "consent"})

    # Not yet connected as an account: the user's live credential is never attached silently.
    started = _start(bridge, client)

    params = _authorize_params(started)
    assert params["scope"] == ["openid email calendar"]
    assert params["prompt"] == ["consent"]
    assert params["access_type"] == ["offline"]
    assert "login_hint" not in params


def test_a_bound_account_credential_is_reused_only_when_it_holds_the_consumer_scopes(monkeypatch) -> None:
    bridge, client = _bridge("scoped-reuse")
    first = _connect(client, bridge.owner, "account-one")
    bridge.attach_credential(first.credential)
    with system_context(reason="test detach bound integration"):
        ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=None, lifecycle="disconnected")
    bridge.refresh_from_db()

    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("mail",))
    started = _start(bridge, client)
    params = _authorize_params(started)
    assert params["login_hint"] == ["account-one@example.com"]
    # The account's grant is asked for again, so the new scope never narrows it.
    assert params["scope"] == ["openid email mail calendar"]

    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))
    assert _start(bridge, client) is None
    bridge.refresh_from_db()
    assert bridge.credential_id == first.credential.pk


def test_connect_is_offered_again_when_the_grant_lacks_a_declared_scope(monkeypatch) -> None:
    bridge, client = _bridge("scope-admission")
    first = _connect(client, bridge.owner, "account-one")
    bridge.attach_credential(first.credential)
    bridge.refresh_from_db()

    with actor_context(bridge.owner):
        monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))
        assert not bridge.can_connect(bridge.owner)
        monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("mail",))
        assert bridge.can_connect(bridge.owner)


def test_a_grant_that_drops_a_served_integrations_scope_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))
    bridge, client = _bridge("scope-downgrade")
    first = _connect(client, bridge.owner, "account-one")
    bridge.attach_credential(first.credential)

    # Granular consent left the calendar box unticked.
    with pytest.raises(OAuthFlowError) as refused:
        _connect(client, bridge.owner, "account-one", scope="openid email")

    assert refused.value.code == SCOPE_NOT_GRANTED
    first.credential.refresh_from_db()
    assert first.credential.granted_scopes == ["openid", "email", "calendar"]


def test_previously_granted_extras_are_asked_for_but_never_required(monkeypatch) -> None:
    bridge, client = _bridge("granted-extras")
    first = _connect(client, bridge.owner, "account-one", scope="openid email mail")
    bridge.attach_credential(first.credential)
    with system_context(reason="test detach bound integration"):
        ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=None, lifecycle="disconnected")
    bridge.refresh_from_db()
    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))

    started = _start(bridge, client)
    assert _authorize_params(started)["scope"] == ["openid email calendar mail"]
    # The user unticks the old mail scope; nothing uses it, so the connect succeeds.
    completed = _connect(client, bridge.owner, "account-one", scope="openid email calendar", state_token=started.state)

    assert completed.credential.granted_scopes == ["openid", "email", "calendar"]


def test_a_provider_that_cannot_be_asked_for_consent_stores_what_it_gave() -> None:
    bridge, client = _bridge("no-consent-prompt")

    stored = _connect(client, bridge.owner, "account-one", refresh=None)

    assert "refresh_token" not in stored.credential.reveal()


def test_a_grant_without_a_refresh_token_restarts_the_connect_asking_for_consent() -> None:
    user = create_user("consent-restart")
    with system_context(reason="test Google consent client"):
        client = OAuthClient.objects.create(
            slug="google", provider_type="google", display_name="Google", client_id="google-client",
            client_secret="fake",
        )
    request = graphql_request(user, session=True)
    started = flow.start(request, client, REDIRECT, user_id=str(user.pk), flow=state.StateFlow.CONNECT)
    assert _authorize_params(started)["prompt"] == ["select_account"]
    provider = OidcProvider(
        issuer="https://accounts.google.com", client_id="google-client",
        token={"access_token": "no-refresh", "expires_in": 3600}, userinfo={"sub": "google-subject"},
    )

    with mock_transport(provider), pytest.raises(ConsentRequired) as refused:
        connect.complete_account_connect(client, code="code", state_token=started.state, redirect_uri=REDIRECT)
    with system_context(reason="test consent refusal wrote nothing"):
        assert not ExternalAccount.objects.filter(oauth_client=client).exists()

    restarted = flow.restart_with_consent(request, client, refused.value.record)
    assert _authorize_params(restarted)["prompt"] == ["select_account consent"]
    # The consent attempt stores what the provider gave; it never loops.
    with mock_transport(provider):
        stored = connect.complete_account_connect(
            client, code="code", state_token=restarted.state, redirect_uri=REDIRECT,
        )
    assert "refresh_token" not in stored.credential.reveal()


def test_connect_identifies_the_account_from_the_id_token_without_userinfo() -> None:
    bridge, client = _bridge("id-token-account", userinfo_endpoint="")
    user = bridge.owner
    provider = OidcProvider(
        issuer=ISSUER,
        client_id=client.client_id,
        token={"access_token": "resource-only", "refresh_token": "refresh", "scope": "openid email mail"},
        id_claims={"sub": "mailbox-subject", "email": "mailbox@example.com", "name": "Mailbox"},
    )
    state_token, _record = state.issue(client, REDIRECT, user_id=str(user.pk), flow=state.StateFlow.CONNECT)

    with mock_transport(provider) as requests:
        result = connect.complete_account_connect(client, code="code", state_token=state_token, redirect_uri=REDIRECT)

    assert not [request for request in requests if request.url.path.endswith("/userinfo")]
    assert (result.account.external_id, result.account.email) == ("mailbox-subject", "mailbox@example.com")
    assert result.account.display_name == "Mailbox"
    assert result.credential.external_account_id == result.account.pk

    forged = OidcProvider(
        issuer=ISSUER, client_id="another-client", token={"access_token": "resource-only"}, id_claims={"sub": "forged"},
    )
    state_token, _record = state.issue(client, REDIRECT, user_id=str(user.pk), flow=state.StateFlow.CONNECT)
    with mock_transport(forged), pytest.raises(OAuthFlowError) as refused:
        connect.complete_account_connect(client, code="code", state_token=state_token, redirect_uri=REDIRECT)
    assert refused.value.code == INVALID_ID_TOKEN


def test_token_response_identity_precedes_the_id_token_for_a_client_without_an_issuer() -> None:
    bridge, client = _bridge("token-identity", issuer="", jwks_uri="", external_id_claim="account.uuid")
    provider = OidcProvider(
        issuer=ISSUER, client_id=client.client_id,
        token={"access_token": "a", "refresh_token": "r", "account": {"uuid": "token-account"}},
        id_claims={"sub": "unverifiable-without-an-issuer"},
    )
    state_token, _record = state.issue(client, REDIRECT, user_id=str(bridge.owner.pk), flow=state.StateFlow.CONNECT)

    with mock_transport(provider):
        result = connect.complete_account_connect(client, code="code", state_token=state_token, redirect_uri=REDIRECT)

    assert result.account.external_id == "token-account"


def _race(*calls: Any) -> list[Any]:
    """Run each call on its own thread and database connection, released together."""

    barrier = threading.Barrier(len(calls))
    outcomes: list[Any] = []

    def run(call: Any) -> None:
        try:
            barrier.wait(timeout=10)
            with system_context(reason="test concurrent oauth writer"):
                outcomes.append(call())
        except OAuthFlowError as error:
            outcomes.append(error.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


@pytest.mark.django_db(transaction=True)
def test_concurrent_attaches_bind_one_account_on_postgresql(monkeypatch) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks")
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    bridge, client = _bridge("attach-race")
    credentials = [_connect(client, bridge.owner, sub).credential for sub in ("account-one", "account-two")]

    def attach(credential: Any) -> Any:
        ConnectionBridge.objects.get(pk=bridge.pk).attach_credential(credential)
        return "attached"

    outcomes = _race(*(lambda credential=credential: attach(credential) for credential in credentials))

    assert sorted(outcomes) == [ACCOUNT_SWITCH_REFUSED, "attached"]


@pytest.mark.django_db(transaction=True)
def test_racing_first_connects_keep_the_refresh_token_on_postgresql() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks")
    bridge, client = _bridge("retention-race")
    user = bridge.owner
    with system_context(reason="test racing account"):
        account = ExternalAccount.objects.link(client, "racing", owner=user)

    def upsert(material: dict[str, str]) -> Any:
        return Credential.objects.upsert_for_user(
            user, client, CredentialKind.OAUTH, material, external_account=account,
        )

    _race(
        lambda: upsert({"access_token": "with", "refresh_token": "kept"}),
        lambda: upsert({"access_token": "without"}),
    )

    with system_context(reason="test racing result"):
        stored = Credential.objects.get(user=user, oauth_client=client, external_account=account)
    assert stored.reveal()["refresh_token"] == "kept"


@pytest.mark.django_db(transaction=True)
def test_legacy_adoption_racing_an_attach_binds_without_deadlock_on_postgresql(monkeypatch) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks")
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    bridge, client = _bridge("adoption-race")
    user = bridge.owner
    with system_context(reason="test legacy credential race"):
        legacy = Credential.objects.upsert_for_user(user, client, CredentialKind.OAUTH, {"access_token": "legacy"})
        ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=legacy)
        other = ConnectionBridge.objects.create(vendor=bridge.vendor, owner=user, lifecycle="disconnected")

    def attach() -> str:
        ConnectionBridge.objects.get(pk=other.pk).attach_credential(Credential.objects.get(pk=legacy.pk))
        return "attached"

    def adopt() -> str:
        _connect(client, user, "account-one")
        return "adopted"

    # Both writers lock integrations before the credential, so neither deadlocks.
    assert sorted(_race(attach, adopt)) == ["adopted", "attached"]

    with system_context(reason="test legacy credential race result"):
        adopted = Credential.objects.select_related("external_account").get(pk=legacy.pk)
        bridge.refresh_from_db()
        other.refresh_from_db()
    assert adopted.external_account.external_id == "account-one"
    assert bridge.account_id == adopted.external_account_id
    # An attach that read the credential before adoption committed stays unbound until its next connect.
    assert other.credential_id == legacy.pk and other.account_id in (None, adopted.external_account_id)
