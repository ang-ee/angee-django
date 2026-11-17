"""One OAuth client, several accounts per user, per-consumer scopes and ID-token identity."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from rebac import actor_context, system_context

from angee.graphql.testing.documents import graphql_request
from angee.integrate import connect
from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth import state
from angee.integrate.oauth.errors import (
    ACCOUNT_SWITCH_REFUSED,
    INVALID_ID_TOKEN,
    SCOPE_NOT_IN_CATALOGUE,
    OAuthFlowError,
)
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.integration import Integration
from angee.integrate.testing.oidc import OidcProvider
from tests.conftest import Credential, OAuthClient, make_integration
from tests.integrate_fixtures import ConnectionAdapter, ConnectionBridge

pytestmark = pytest.mark.django_db

REDIRECT = "https://app.example/integrate/oauth/callback"
ISSUER = "https://provider.example"


def _bridge(slug: str, **client_fields: Any) -> ConnectionBridge:
    """Create an owned bridge whose OAuth client answers the fake OpenID provider."""

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
    return bridge


def _client(bridge: ConnectionBridge) -> OAuthClient:
    with system_context(reason="test oauth accounts client read"):
        return OAuthClient.objects.get(pk=bridge.credential.oauth_client_id)


def _connect(client: OAuthClient, user: Any, sub: str, *, refresh: str | None = "refresh", **flow: Any) -> Any:
    """Complete one account connect for ``sub`` through the fake provider."""

    token = {"access_token": f"access-{sub}", "expires_in": 3600, "scope": "openid email calendar"}
    if refresh is not None:
        token["refresh_token"] = f"{refresh}-{sub}"
    provider = OidcProvider(
        issuer=ISSUER, client_id=client.client_id, token=token, userinfo={"sub": sub, "email": f"{sub}@example.com"},
    )
    state_token = flow.pop("state_token", None)
    if state_token is None:
        state_token, _record = state.issue(client, REDIRECT, user_id=str(user.pk), flow=state.StateFlow.CONNECT)
    with mock_transport(provider):
        return connect.complete_account_connect(client, code="code", state_token=state_token, redirect_uri=REDIRECT)


def _authorize_params(started: Any) -> dict[str, list[str]]:
    return parse_qs(urlsplit(started.authorize_url).query)


def test_one_user_connects_two_accounts_through_one_client_and_both_stay_live() -> None:
    bridge = _bridge("two-accounts")
    client, user = _client(bridge), bridge.owner

    first = _connect(client, user, "account-one")
    second = _connect(client, user, "account-two")

    assert first.credential.pk != second.credential.pk
    for completion in (first, second):
        live = Credential.objects.live_oauth_for_user(user, client, external_account=completion.account)
        assert live is not None and live.pk == completion.credential.pk
        assert live.reveal()["refresh_token"] == f"refresh-{completion.account.external_id}"


def test_reconnecting_one_account_never_touches_another() -> None:
    bridge = _bridge("reconnect-isolation")
    client, user = _client(bridge), bridge.owner
    first = _connect(client, user, "account-one")
    second = _connect(client, user, "account-two")
    bridge.attach_credential(second.credential)
    bridge.refresh_from_db()
    with system_context(reason="test reconnect isolation snapshot"):
        untouched = Credential.objects.get(pk=second.credential.pk)
    generation = bridge.discovery_generation

    # Google re-consent returns no refresh token; the stored one is kept.
    again = _connect(client, user, "account-one", refresh=None)

    assert again.credential.pk == first.credential.pk
    assert again.credential.reveal()["access_token"] == "access-account-one"
    assert again.credential.reveal()["refresh_token"] == "refresh-account-one"
    with system_context(reason="test reconnect isolation assertion"):
        after = Credential.objects.get(pk=second.credential.pk)
    assert (after.material, after.updated_at) == (untouched.material, untouched.updated_at)
    bridge.refresh_from_db()
    assert (bridge.credential_id, bridge.discovery_generation) == (second.credential.pk, generation)


def test_connecting_another_account_into_a_bound_integration_is_refused() -> None:
    bridge = _bridge("account-switch")
    client, user = _client(bridge), bridge.owner
    first = _connect(client, user, "account-one")
    bridge.attach_credential(first.credential)
    with system_context(reason="test account switch needs consent"):
        Credential.objects.filter(pk=first.credential.pk).update(last_refresh_status="failed")
    bridge.refresh_from_db()

    started = connect.connect_integration_target(
        bridge, client, user=user, request=graphql_request(user, session=True), redirect_uri=REDIRECT, next_path="/",
    )
    params = _authorize_params(started)
    assert params["prompt"] == ["select_account"]
    assert params["login_hint"] == ["account-one@example.com"]

    switched = _connect(client, user, "account-two", state_token=started.state)
    with actor_context(user), pytest.raises(OAuthFlowError) as refused:
        Integration.objects.attach_completed_connect(
            switched.integration_id, user, switched.credential, exposed_model_labels={ConnectionBridge._meta.label},
        )

    assert refused.value.code == ACCOUNT_SWITCH_REFUSED
    bridge.refresh_from_db()
    assert (bridge.credential_id, bridge.account_id) == (first.credential.pk, first.account.pk)


def test_consumer_scopes_join_the_defaults_and_must_be_in_the_catalogue() -> None:
    client = OAuthClient(slug="scoped", default_scopes=["openid", "email"], scopes_catalogue=["calendar", "mail"])

    assert client.requested_scopes() == ["openid", "email"]
    assert client.requested_scopes(["calendar", "email", "calendar"]) == ["openid", "email", "calendar"]
    with pytest.raises(OAuthFlowError) as refused:
        client.requested_scopes(["drive"])
    assert refused.value.code == SCOPE_NOT_IN_CATALOGUE


def test_connect_requests_the_consumer_scopes_and_lets_the_user_choose_the_account(monkeypatch) -> None:
    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))
    bridge = _bridge("scoped-connect", authorize_params={"access_type": "offline", "prompt": "consent"})
    client, user = _client(bridge), bridge.owner
    with system_context(reason="test unbound integration"):
        ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=None, account=None, lifecycle="disconnected")
    bridge.refresh_from_db()

    # Not yet connected as an account: the user's live credential is never attached silently.
    started = connect.connect_integration_target(
        bridge, client, user=user, request=graphql_request(user, session=True), redirect_uri=REDIRECT, next_path="/",
    )

    params = _authorize_params(started)
    assert params["scope"] == ["openid email calendar"]
    assert params["prompt"] == ["consent select_account"]
    assert params["access_type"] == ["offline"]
    assert "login_hint" not in params


def test_a_bound_account_credential_is_reused_only_when_it_holds_the_consumer_scopes(monkeypatch) -> None:
    bridge = _bridge("scoped-reuse")
    client, user = _client(bridge), bridge.owner
    first = _connect(client, user, "account-one")
    bridge.attach_credential(first.credential)
    request = graphql_request(user, session=True)

    def detach() -> None:
        with system_context(reason="test detach bound integration"):
            ConnectionBridge.objects.filter(pk=bridge.pk).update(credential=None, lifecycle="disconnected")
        bridge.refresh_from_db()

    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("mail",))
    detach()
    started = connect.connect_integration_target(
        bridge, client, user=user, request=request, redirect_uri=REDIRECT, next_path="/",
    )
    assert started is not None and _authorize_params(started)["login_hint"] == ["account-one@example.com"]

    monkeypatch.setattr(ConnectionAdapter, "oauth_scopes", ("calendar",))
    assert connect.connect_integration_target(
        bridge, client, user=user, request=request, redirect_uri=REDIRECT, next_path="/",
    ) is None
    bridge.refresh_from_db()
    assert bridge.credential_id == first.credential.pk


def test_connect_identifies_the_account_from_the_id_token_without_userinfo() -> None:
    bridge = _bridge("id-token-account", userinfo_endpoint="")
    client, user = _client(bridge), bridge.owner
    provider = OidcProvider(
        issuer=ISSUER,
        client_id=client.client_id,
        token={"access_token": "resource-only", "scope": "openid email mail"},
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
