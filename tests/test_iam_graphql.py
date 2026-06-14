"""Tests for IAM connection GraphQL surfaces."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext, override_settings
from rebac import app_settings, system_context
from rebac.roles import grant
from strawberry import relay

from angee.iam import identity
from angee.iam.credentials import CredentialKind
from angee.iam.oidc.errors import OidcFlowError
from tests.conftest import Credential, ExternalAccount, OAuthClient, addon_schema, execute_schema
from tests.conftest import _create_missing_tables as _create_connection_tables
from tests.conftest import result_data as _data

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")


def test_available_connections_returns_only_enabled_oauth_clients_without_secret_fields(
    iam_connection_tables: None,
) -> None:
    """The public picker is system-scoped but only exposes safe configured OIDC rows."""

    _oauth_client("enabled", is_oidc=True, is_enabled=True, client_secret="secret")
    _oauth_client("disabled", is_oidc=True, is_enabled=False, client_secret="secret")
    _oauth_client("oauth", is_oidc=False, is_enabled=True, client_secret="secret")
    _oauth_client("no-client", is_oidc=True, is_enabled=True, client_id="", client_secret="secret")
    _oauth_client(
        "no-endpoints",
        is_oidc=True,
        is_enabled=True,
        authorize_endpoint="",
        discovery_url="",
        client_secret="secret",
    )
    public_schema = _schema("public")

    data = _data(
        _execute(
            public_schema,
            """
            query {
              availableConnections(pagination: {limit: 10}) {
                totalCount
                results {
                  oauthClientSqid
                  oauthClientDisplayName
                  oauthClientSlug
                  oauthClientIcon
                  isOidc
                }
              }
            }
            """,
        )
    )

    connections = data["availableConnections"]["results"]
    assert data["availableConnections"]["totalCount"] == 1
    assert [row["oauthClientSlug"] for row in connections] == ["enabled"]
    assert connections[0]["isOidc"] is True
    assert "clientSecret" not in public_schema.as_str()


def test_available_connections_reads_client_columns_without_per_row_queries(
    iam_connection_tables: None,
) -> None:
    """The picker reads the self-describing client's own columns, so its query count stays flat as rows grow."""

    query = """
        query {
          availableConnections(pagination: {limit: 10}) {
            totalCount
            results { oauthClientSqid oauthClientSlug oauthClientDisplayName oauthClientIcon }
          }
        }
    """
    _oauth_client("solo", is_oidc=True, is_enabled=True)
    public_schema = _schema("public")
    with CaptureQueriesContext(connection) as one_row:
        _data(_execute(public_schema, query))

    _oauth_client("dup-a", is_oidc=True, is_enabled=True)
    _oauth_client("dup-b", is_oidc=True, is_enabled=True)
    with CaptureQueriesContext(connection) as three_rows:
        data = _data(_execute(public_schema, query))

    assert data["availableConnections"]["totalCount"] == 3
    assert len(three_rows.captured_queries) == len(one_row.captured_queries)


def test_login_start_rejects_non_oidc_or_disabled_oauth_client(
    iam_connection_tables: None,
) -> None:
    """OIDC start fails closed when the selected OAuth client cannot run login."""

    non_oidc = _oauth_client("oauth", is_oidc=False, is_enabled=True)
    disabled = _oauth_client("off", is_oidc=True, is_enabled=False)
    public_schema = _schema("public")
    query = """
        mutation LoginStart($oauthClientSqid: String!) {
          loginStart(
            oauthClientSqid: $oauthClientSqid,
            redirectUri: "https://app.example/callback"
          ) {
            state
          }
        }
    """

    for oauth_client in (non_oidc, disabled):
        result = _execute(public_schema, query, {"oauthClientSqid": oauth_client.sqid})

        assert result.errors is not None
        assert "enabled for OIDC" in result.errors[0].message


def test_login_start_returns_oidc_flow_error_payload(
    iam_connection_tables: None,
) -> None:
    """Start-flow OIDC errors are returned as typed payloads, not GraphQL errors."""

    oauth_client = _oauth_client(
        "misconfigured",
        is_oidc=True,
        is_enabled=True,
        authorize_endpoint="",
        discovery_url="",
    )
    public_schema = _schema("public")

    data = _data(
        _execute(
            public_schema,
            """
            mutation LoginStart($oauthClientSqid: String!) {
              loginStart(
                oauthClientSqid: $oauthClientSqid,
                redirectUri: "https://app.example/callback"
              ) {
                authorizeUrl
                state
                error
                errorCode
              }
            }
            """,
            {"oauthClientSqid": oauth_client.sqid},
        )
    )

    assert data["loginStart"] == {
        "authorizeUrl": "",
        "state": "",
        "error": "missing_endpoint",
        "errorCode": "missing_endpoint",
    }


@override_settings(
    AUTHENTICATION_BACKENDS=(
        "rebac.backends.auth.RebacBackend",
        "django.contrib.auth.backends.ModelBackend",
    )
)
def test_login_complete_provisions_and_logs_in(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OIDC completion delegates identity resolution then writes the session."""

    oauth_client = _oauth_client("oidc", is_oidc=True, is_enabled=True)
    user = User.objects.create_user(
        username="oidc-user",
        email="oidc@example.com",
    )
    public_schema = _schema("public")
    request = _request(AnonymousUser())

    start = _data(
        _execute(
            public_schema,
            """
            mutation {
              loginStart(
                oauthClientSqid: "%s",
                redirectUri: "https://app.example/callback",
                next: "/after-login"
              ) {
                authorizeUrl
                state
              }
            }
            """
            % oauth_client.sqid,
            request=request,
        )
    )["loginStart"]

    def complete_login(
        selected_oauth_client: OAuthClient,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> Any:
        assert selected_oauth_client.pk == oauth_client.pk
        assert code == "code"
        assert state_token == start["state"]
        assert redirect_uri == "https://app.example/callback"
        return identity.LoginCompletion(
            user=user,
            claims={"sub": "sub-login", "email": "oidc@example.com"},
            next_path="/after-login",
        )

    monkeypatch.setattr(iam_schema.identity, "complete_login", complete_login)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              loginComplete(
                code: "code",
                state: $state,
                redirectUri: "https://app.example/callback"
              ) {
                ok
                intent
                next
                claims
                error
                errorCode
                user { username }
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert completed["loginComplete"] == {
        "ok": True,
        "intent": "login",
        "next": "/after-login",
        "claims": {"sub": "sub-login", "email": "oidc@example.com"},
        "error": None,
        "errorCode": None,
        "user": {"username": "oidc-user"},
    }
    assert request.session[SESSION_KEY] == str(user.pk)
    assert request.session[BACKEND_SESSION_KEY] == "django.contrib.auth.backends.ModelBackend"


def test_login_complete_returns_oidc_flow_error_payload(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion OIDC errors are returned as typed payloads, not GraphQL errors."""

    oauth_client = _oauth_client("oidc-error", is_oidc=True, is_enabled=True)
    public_schema = _schema("public")
    request = _request(AnonymousUser())
    start = _data(
        _execute(
            public_schema,
            """
            mutation {
              loginStart(
                oauthClientSqid: "%s",
                redirectUri: "https://app.example/callback"
              ) {
                state
              }
            }
            """
            % oauth_client.sqid,
            request=request,
        )
    )["loginStart"]

    def complete_login(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise OidcFlowError("invalid_id_token", 400, "bad token")

    monkeypatch.setattr(iam_schema.identity, "complete_login", complete_login)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              loginComplete(
                code: "bad-code",
                state: $state,
                redirectUri: "https://app.example/callback"
              ) {
                ok
                user { username }
                intent
                next
                claims
                error
                errorCode
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert completed["loginComplete"] == {
        "ok": False,
        "user": None,
        "intent": "login",
        "next": "/",
        "claims": None,
        "error": "bad token",
        "errorCode": "invalid_id_token",
    }


def test_link_account_complete_returns_account_claims_intent_and_coerced_next(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Link completion returns rich result fields and uses the safe stored next path."""

    user = User.objects.create_user(username="link-user", email="link@example.com")
    oauth_client = _oauth_client("link-rich", is_oidc=True, is_enabled=True)
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-link-rich",
        owner=user,
        email="link@example.com",
    )
    public_schema = _schema("public")
    request = _request(user)
    start = _data(
        _execute(
            public_schema,
            """
            mutation {
              linkAccountStart(
                oauthClientSqid: "%s",
                redirectUri: "https://app.example/callback",
                next: "https://evil.example/phish"
              ) {
                state
              }
            }
            """
            % oauth_client.sqid,
            request=request,
        )
    )["linkAccountStart"]

    def complete_link(
        selected_oauth_client: OAuthClient,
        selected_user: Any | None = None,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> Any:
        del selected_user
        assert selected_oauth_client.pk == oauth_client.pk
        assert code == "code"
        assert redirect_uri == "https://app.example/callback"
        record = iam_schema.state.consume(state_token)
        assert record.next_path == "/"
        return identity.LinkCompletion(
            account=account,
            user=user,
            claims={"sub": "sub-link-rich", "email": "link@example.com"},
            next_path=record.next_path or "/",
        )

    monkeypatch.setattr(iam_schema.identity, "complete_link", complete_link)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              linkAccountComplete(
                code: "code",
                state: $state,
                redirectUri: "https://app.example/callback"
              ) {
                account { externalId }
                user { username }
                intent
                next
                claims
                error
                errorCode
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert completed["linkAccountComplete"] == {
        "account": {"externalId": "sub-link-rich"},
        "user": {"username": "link-user"},
        "intent": "link",
        "next": "/",
        "claims": {"sub": "sub-link-rich", "email": "link@example.com"},
        "error": None,
        "errorCode": None,
    }


def test_connect_account_complete_surfaces_provider_error_message(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connect completion keeps the stable code but shows provider error text."""

    user = User.objects.create_user(username="connect-rate-limited", email="connect@example.com")
    oauth_client = _oauth_client("connect-anthropic", is_oidc=False)
    public_schema = _schema("public")
    request = _request(user)
    oauth_client_id = relay.to_base64("OAuthClientType", oauth_client.sqid)
    start = _data(
        _execute(
            public_schema,
            """
            mutation Start($id: ID!) {
              connectAccountStart(
                id: $id,
                redirectUri: "https://app.example/callback"
              ) {
                state
                error
                errorCode
              }
            }
            """,
            {"id": oauth_client_id},
            request=request,
        )
    )["connectAccountStart"]

    def complete_account_connect(
        selected_oauth_client: OAuthClient,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> Any:
        del code, state_token, redirect_uri
        assert selected_oauth_client.pk == oauth_client.pk
        raise OidcFlowError(
            "token_exchange_failed",
            429,
            body={
                "error": {
                    "type": "rate_limit_error",
                    "message": "Rate limited. Please try again later.",
                },
            },
        )

    monkeypatch.setattr(iam_schema.identity, "complete_account_connect", complete_account_connect)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              connectAccountComplete(
                code: "code",
                state: $state,
                redirectUri: "https://app.example/callback"
              ) {
                account { externalId }
                credential { displayName }
                error
                errorCode
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert start["error"] is None
    assert completed["connectAccountComplete"] == {
        "account": None,
        "credential": None,
        "error": "Rate limited. Please try again later.",
        "errorCode": "token_exchange_failed",
    }


def test_oauth_client_crud_are_admin_only(
    iam_connection_tables: None,
) -> None:
    """Console CRUD is denied to non-admins and allowed for platform admins."""

    user = User.objects.create_user(username="plain", email="plain@example.com")
    admin = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    console_schema = _schema("console")
    create_oauth_client = """
        mutation CreateOAuthClient {
          createOauthClient(data: {
            slug: "console",
            icon: "console.svg",
            displayName: "Console prod",
            clientId: "console-client",
            clientSecret: "console-secret",
            isOidc: true,
            isEnabled: true,
            authorizeEndpoint: "https://issuer.example/authorize",
            tokenEndpoint: "https://issuer.example/token"
          }) {
            id
            slug
            icon
            displayName
            isOidc
            configurationState
            clientSecret
          }
        }
    """

    denied = _execute(console_schema, create_oauth_client, user=user)
    assert denied.errors is not None

    oauth_client = _data(
        _execute(console_schema, create_oauth_client, user=admin)
    )["createOauthClient"]
    oauth_client_id = oauth_client["id"]
    assert oauth_client["slug"] == "console"
    assert oauth_client["icon"] == "console.svg"
    assert oauth_client["displayName"] == "Console prod"
    assert oauth_client["isOidc"] is True
    assert oauth_client["configurationState"] == "ready"
    assert oauth_client["clientSecret"] == "console-secret"
    with system_context(reason="test.iam.oauth_client_secret"):
        stored_client = OAuthClient.objects.get(client_id="console-client")
    assert stored_client.client_secret == "console-secret"

    external_account_mutation = """
        mutation CreateExternalAccount($oauthClient: ID!, $owner: String!) {
          createExternalAccount(data: {
            oauthClient: $oauthClient,
            owner: $owner,
            externalId: "admin-sub",
            email: "admin@example.com",
            displayName: "Admin OIDC",
            status: "active"
          }) {
            id
            externalId
            email
            displayName
            status
            providerSlug
          }
        }
    """
    linked = _data(
        _execute(
            console_schema,
            external_account_mutation,
            {"oauthClient": oauth_client_id, "owner": str(admin.pk)},
            user=admin,
        )
    )["createExternalAccount"]
    assert linked["externalId"] == "admin-sub"
    assert linked["email"] == "admin@example.com"
    assert linked["providerSlug"] == "console"
    with system_context(reason="test.iam.external_account"):
        account = ExternalAccount.objects.get(external_id="admin-sub")
    assert ExternalAccount.objects.owner_for(account) == admin
    assert _execute(
        console_schema,
        external_account_mutation,
        {"oauthClient": oauth_client_id, "owner": str(admin.pk)},
        user=user,
    ).errors is not None


def test_reveal_credential_returns_the_secret_and_is_admin_only(
    iam_connection_tables: None,
) -> None:
    """Reveal decrypts the stored secret for an admin only; the read type never carries it."""

    plain = User.objects.create_user(username="reveal-plain", email="plain@example.com")
    admin = _platform_admin("reveal-admin")
    console_schema = _schema("console")

    create_credential = """
        mutation CreateCredential {
          createCredential(data: {name: "GitHub PAT", kind: "static_token", apiKey: "ghp_secret_value"}) {
            id
          }
        }
    """
    credential_id = _data(_execute(console_schema, create_credential, user=admin))["createCredential"]["id"]

    reveal = "mutation Reveal($id: ID!) { revealCredential(id: $id) { secret } }"
    revealed = _data(_execute(console_schema, reveal, {"id": credential_id}, user=admin))["revealCredential"]
    assert revealed["secret"] == "ghp_secret_value"

    # A non-admin cannot reveal another principal's secret.
    assert _execute(console_schema, reveal, {"id": credential_id}, user=plain).errors is not None


def test_user_crud_create_update_delete_are_admin_only(
    iam_connection_tables: None,
) -> None:
    """User CRUD hashes the write-only password, never echoes it, and is admin gated."""

    plain = User.objects.create_user(username="user-crud-plain", email="plain@example.com")
    admin = _platform_admin("user-crud-admin")
    console_schema = _schema("console")
    create_user = """
        mutation CreateUser {
          createUser(data: {
            username: "console-user",
            password: "first-secret",
            email: "console-user@example.com",
            firstName: "Console",
            lastName: "User",
            isStaff: true,
            isActive: true
          }) {
            username
            email
            firstName
            lastName
            isStaff
            isActive
            fullName
          }
        }
    """

    assert _execute(console_schema, create_user, user=plain).errors is not None

    created = _data(_execute(console_schema, create_user, user=admin))["createUser"]
    assert created == {
        "username": "console-user",
        "email": "console-user@example.com",
        "firstName": "Console",
        "lastName": "User",
        "isStaff": True,
        "isActive": True,
        "fullName": "Console User",
    }
    # ``password`` is write-only: it is neither a field on ``UserType`` nor in its SDL.
    assert "password" not in _sdl_block(console_schema.as_str(), "type UserType")
    with system_context(reason="test.iam.user_crud.create"):
        user = User.objects.get(username="console-user")
        assert user.check_password("first-secret")
    user_id = _user_global_id(user)

    changed = _data(
        _execute(
            console_schema,
            """
            mutation UpdateUser($id: ID!) {
              updateUser(data: {id: $id, firstName: "Renamed", isStaff: false}) {
                firstName
                isStaff
              }
            }
            """,
            {"id": user_id},
            user=admin,
        )
    )["updateUser"]
    assert changed == {"firstName": "Renamed", "isStaff": False}
    with system_context(reason="test.iam.user_crud.update_field"):
        user.refresh_from_db()
        assert user.first_name == "Renamed"
        assert user.is_staff is False
        assert user.check_password("first-secret")

    _data(
        _execute(
            console_schema,
            """
            mutation RehashUser($id: ID!) {
              updateUser(data: {id: $id, password: "second-secret"}) {
                username
              }
            }
            """,
            {"id": user_id},
            user=admin,
        )
    )
    with system_context(reason="test.iam.user_crud.update_password"):
        user.refresh_from_db()
        assert user.check_password("second-secret")
        assert not user.check_password("first-secret")

    assert _execute(
        console_schema,
        """
        mutation DeleteUser($id: ID!) {
          deleteUser(id: $id, confirm: true) { totalDeletedCount }
        }
        """,
        {"id": user_id},
        user=plain,
    ).errors is not None

    deleted = _data(
        _execute(
            console_schema,
            """
            mutation DeleteUser($id: ID!) {
              deleteUser(id: $id, confirm: true) {
                hasBlockers
                totalDeletedCount
              }
            }
            """,
            {"id": user_id},
            user=admin,
        )
    )["deleteUser"]
    assert deleted["hasBlockers"] is False
    assert deleted["totalDeletedCount"] >= 1
    with system_context(reason="test.iam.user_crud.delete"):
        assert not User.objects.filter(pk=user.pk).exists()


def test_external_account_update_delete_are_admin_only(
    iam_connection_tables: None,
) -> None:
    """Updating then deleting an external account revokes its owner tuple; admin gated."""

    plain = User.objects.create_user(username="ea-update-plain", email="plain@example.com")
    admin = _platform_admin("ea-update-admin")
    oauth_client = _oauth_client("ea-update")
    account = ExternalAccount.objects.link(
        oauth_client,
        "ea-update-sub",
        owner=admin,
        email="before@example.com",
        display_name="Before",
        status="active",
    )
    account_id = relay.to_base64("ExternalAccountType", account.sqid)
    console_schema = _schema("console")
    update_account = """
        mutation UpdateExternalAccount($id: ID!) {
          updateExternalAccount(data: {
            id: $id,
            email: "after@example.com",
            displayName: "After",
            status: "revoked"
          }) {
            email
            displayName
            status
          }
        }
    """

    assert _execute(console_schema, update_account, {"id": account_id}, user=plain).errors is not None

    updated = _data(
        _execute(console_schema, update_account, {"id": account_id}, user=admin)
    )["updateExternalAccount"]
    # ``status`` is a choices field exposed as a GraphQL enum, so it renders as the
    # uppercase member name though the write input takes the raw ``"revoked"`` value.
    assert updated == {
        "email": "after@example.com",
        "displayName": "After",
        "status": "REVOKED",
    }
    with system_context(reason="test.iam.external_account.owner_before_delete"):
        assert ExternalAccount.objects.owner_for(account) == admin

    delete_account = """
        mutation DeleteExternalAccount($id: ID!) {
          deleteExternalAccount(id: $id, confirm: true) {
            hasBlockers
            totalDeletedCount
          }
        }
    """

    assert _execute(console_schema, delete_account, {"id": account_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_account, {"id": account_id}, user=admin)
    )["deleteExternalAccount"]
    assert deleted["hasBlockers"] is False
    assert deleted["totalDeletedCount"] >= 1
    with system_context(reason="test.iam.external_account.after_delete"):
        assert not ExternalAccount.objects.filter(pk=account.pk).exists()
        assert ExternalAccount.objects.owner_for(account) is None


def test_credential_crud_create_delete_are_admin_only(
    iam_connection_tables: None,
) -> None:
    """Creating a static-token credential renders ``displayName``; delete is admin gated."""

    plain = User.objects.create_user(username="cred-crud-plain", email="plain@example.com")
    admin = _platform_admin("cred-crud-admin")
    owner = User.objects.create_user(username="cred-crud-owner", email="owner@example.com")
    console_schema = _schema("console")
    create_credential = """
        mutation CreateCredential($user: ID!) {
          createCredential(data: {
            user: $user,
            name: "ci-token",
            kind: "static_token",
            apiKey: "static-token-value"
          }) {
            kind
            name
            status
            displayName
          }
        }
    """
    variables = {"user": _user_global_id(owner)}

    assert _execute(console_schema, create_credential, variables, user=plain).errors is not None

    created = _data(
        _execute(console_schema, create_credential, variables, user=admin)
    )["createCredential"]
    assert created["name"] == "ci-token"
    # A provider-less static token reads its own name as the label.
    assert created["displayName"] == "ci-token"
    # ``api_key`` is write-only: it never surfaces on ``CredentialType``.
    assert "apiKey" not in _sdl_block(console_schema.as_str(), "type CredentialType")
    with system_context(reason="test.iam.credential_crud.create"):
        credential = Credential.objects.get(user=owner, name="ci-token")
        assert credential.kind == CredentialKind.STATIC_TOKEN
        assert credential.oauth_client_id is None
    credential_id = relay.to_base64("CredentialType", credential.sqid)

    delete_credential = """
        mutation DeleteCredential($id: ID!) {
          deleteCredential(id: $id, confirm: true) {
            hasBlockers
            totalDeletedCount
          }
        }
    """

    assert _execute(console_schema, delete_credential, {"id": credential_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_credential, {"id": credential_id}, user=admin)
    )["deleteCredential"]
    assert deleted["hasBlockers"] is False
    assert deleted["totalDeletedCount"] >= 1
    with system_context(reason="test.iam.credential_crud.delete"):
        assert not Credential.objects.filter(pk=credential.pk).exists()


def test_console_external_accounts_render_provider_projection(
    iam_connection_tables: None,
) -> None:
    """The admin externalAccounts list renders provider_* through the guarded join.

    Exercises ``console_external_accounts()``'s ``rebac_select_related`` path (not
    the write path), which runs the actor-scope guard over the joined OAuth client
    — see memory ``rebac-select-related-actor-scope-trap``.
    """

    admin = User.objects.create_superuser(
        username="ea-list-admin",
        email="ea-list-admin@example.com",
        password="x",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    oauth_client = _oauth_client("listco", display_name="ListCo prod")
    ExternalAccount.objects.link(oauth_client, "list-sub", owner=admin, email="u@example.com")

    accounts = _data(
        _execute(
            _schema("console"),
            """
            query {
              externalAccounts {
                results { externalId providerSlug providerEnvironment providerLabel }
              }
            }
            """,
            user=admin,
        )
    )["externalAccounts"]["results"]
    row = next(item for item in accounts if item["externalId"] == "list-sub")
    assert row["providerSlug"] == "listco"
    assert row["providerEnvironment"] == "prod"
    assert row["providerLabel"] == "ListCo prod"


def test_oauth_client_secret_is_console_readable_and_public_hidden(
    iam_connection_tables: None,
) -> None:
    """OAuth client secrets are admin-readable while remaining absent publicly."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "clientSecret" not in public_sdl
    assert "clientSecret" in _sdl_block(console_sdl, "type OAuthClientType")
    assert "clientSecret" in _sdl_block(console_sdl, "input OAuthClientInput")
    assert "clientSecret" in _sdl_block(console_sdl, "input OAuthClientPatch")
    for sdl in (public_sdl, console_sdl):
        assert "material" not in sdl
        assert "identityClaims" not in sdl


def test_account_connect_schema_exposes_generic_flow_without_token_material(
    iam_connection_tables: None,
) -> None:
    """IAM exposes account-connect mutations and OAuth metadata without token values."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "connectAccountStart(" in _sdl_block(public_sdl, "type Mutation")
    assert "connectAccountComplete(" in _sdl_block(public_sdl, "type Mutation")
    assert "credential: CredentialType" in _sdl_block(public_sdl, "type ConnectAccountResult")
    oauth_client_type = _sdl_block(console_sdl, "type OAuthClientType")
    oauth_client_input = _sdl_block(console_sdl, "input OAuthClientInput")
    oauth_client_patch = _sdl_block(console_sdl, "input OAuthClientPatch")
    for field in ("authorizeParams", "tokenParams", "tokenRequestFormat", "externalIdClaim", "emailClaim"):
        assert field in oauth_client_type
        assert field in oauth_client_input
        assert field in oauth_client_patch
    assert "accessToken" not in public_sdl
    assert "refreshToken" not in public_sdl


def test_console_schema_exposes_user_change_subscription(
    iam_connection_tables: None,
) -> None:
    """The IAM users view has a console change stream to subscribe to."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "userChanged" not in public_sdl
    assert "userChanged: ChangeEvent!" in _sdl_block(console_sdl, "type Subscription")


def test_my_connected_accounts_are_scoped_to_session_user(
    iam_connection_tables: None,
) -> None:
    """A user's connected-account page excludes another user's credentials."""

    alice = User.objects.create_user(username="alice", email="alice@example.com")
    bob = User.objects.create_user(username="bob", email="bob@example.com")
    oauth_client = _oauth_client("scope", is_oidc=True, is_enabled=True)
    alice_account = ExternalAccount.objects.link(
        oauth_client,
        "alice-ext",
        owner=alice,
        email="alice@vendor.example",
    )
    bob_account = ExternalAccount.objects.link(
        oauth_client,
        "bob-ext",
        owner=bob,
        email="bob@vendor.example",
    )
    alice_credential = Credential.objects.upsert_for_user(
        alice,
        oauth_client,
        CredentialKind.STATIC_TOKEN,
        {"api_key": "alice-token"},
        external_account=alice_account,
    )
    Credential.objects.upsert_for_user(
        bob,
        oauth_client,
        CredentialKind.STATIC_TOKEN,
        {"api_key": "bob-token"},
        external_account=bob_account,
    )
    with system_context(reason="test iam graphql setup"):
        alice_account.credential = alice_credential
        alice_account.save(update_fields=["credential", "updated_at"])

    data = _data(
        _execute(
            _schema("public"),
            """
            query {
              myConnectedAccounts(pagination: {limit: 10}) {
                results {
                  status
                  externalAccount { externalId email credentialStatus }
                }
              }
            }
            """,
            user=alice,
        )
    )

    accounts = data["myConnectedAccounts"]["results"]
    assert [row["externalAccount"]["externalId"] for row in accounts] == ["alice-ext"]
    assert accounts[0]["externalAccount"]["email"] == "alice@vendor.example"
    assert accounts[0]["externalAccount"]["credentialStatus"] == "active"


def test_unlink_account_only_removes_callers_credential(
    iam_connection_tables: None,
) -> None:
    """Unlinking deletes the session user's credential and leaves others alone."""

    alice = User.objects.create_user(username="unlink-alice", email="alice@example.com")
    bob = User.objects.create_user(username="unlink-bob", email="bob@example.com")
    oauth_client = _oauth_client("unlink", is_oidc=True, is_enabled=True)
    account = ExternalAccount.objects.link(
        oauth_client,
        "shared-ext",
        owner=alice,
        email="shared@example.com",
    )
    Credential.objects.upsert_for_user(
        alice,
        oauth_client,
        CredentialKind.STATIC_TOKEN,
        {"api_key": "alice-token"},
        external_account=account,
    )
    Credential.objects.upsert_for_user(
        bob,
        oauth_client,
        CredentialKind.STATIC_TOKEN,
        {"api_key": "bob-token"},
        external_account=account,
    )

    data = _data(
        _execute(
            _schema("public"),
            """
            mutation Unlink($sqid: String!) {
              unlinkAccount(externalAccountSqid: $sqid) {
                ok
                error
                errorCode
              }
            }
            """,
            {"sqid": account.sqid},
            user=alice,
        )
    )

    assert data["unlinkAccount"] == {"ok": True, "error": None, "errorCode": None}
    with system_context(reason="test assertions"):
        assert not Credential.objects.filter(user=alice, external_account=account).exists()
        assert Credential.objects.filter(user=bob, external_account=account).exists()


def test_unlink_account_revokes_owner_so_oidc_login_is_blocked(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlinking an OIDC credential revokes ownership so future OIDC login fails."""

    user = User.objects.create_user(
        username="unlink-oidc-owner",
        email="owner@example.com",
        password="password",
    )
    oauth_client = _oauth_client(
        "unlink-oidc",
        is_oidc=True,
        is_enabled=True,
        revoke_endpoint="https://issuer.example/revoke",
    )
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-unlinked",
        owner=user,
        email="owner@example.com",
    )
    Credential.objects.upsert_for_user(
        user,
        oauth_client,
        CredentialKind.OAUTH,
        {"access_token": "unlink-access"},
        external_account=account,
    )
    revoked_tokens: list[str] = []

    def revoke_token(selected_oauth_client: Any, token: str) -> None:
        assert selected_oauth_client.pk == oauth_client.pk
        revoked_tokens.append(token)

    monkeypatch.setattr(iam_schema.client_module, "revoke_token", revoke_token)

    data = _data(
        _execute(
            _schema("public"),
            """
            mutation Unlink($sqid: String!) {
              unlinkAccount(externalAccountSqid: $sqid) {
                ok
                error
                errorCode
              }
            }
            """,
            {"sqid": account.sqid},
            user=user,
        )
    )

    assert data["unlinkAccount"] == {"ok": True, "error": None, "errorCode": None}
    assert revoked_tokens == ["unlink-access"]
    with system_context(reason="test assertions"):
        assert ExternalAccount.objects.owner_for(account) is None
        assert not Credential.objects.filter(user=user, external_account=account).exists()
    with pytest.raises(OidcFlowError):
        identity.resolve(
            oauth_client,
            sub="sub-unlinked",
            email="owner@example.com",
            claims={"sub": "sub-unlinked"},
        )


def test_unlink_account_blocks_last_oidc_sign_in_method_for_passwordless_user(
    iam_connection_tables: None,
) -> None:
    """Passwordless users cannot remove their last OIDC sign-in credential."""

    user = User.objects.create_user(username="unlink-passwordless", email="passwordless@example.com")
    oauth_client = _oauth_client("unlink-guard", is_oidc=True, is_enabled=True)
    account = ExternalAccount.objects.link(
        oauth_client,
        "sub-guard",
        owner=user,
        email="passwordless@example.com",
    )
    Credential.objects.upsert_for_user(
        user,
        oauth_client,
        CredentialKind.OAUTH,
        {"access_token": "guard-access"},
        external_account=account,
    )

    data = _data(
        _execute(
            _schema("public"),
            """
            mutation Unlink($sqid: String!) {
              unlinkAccount(externalAccountSqid: $sqid) {
                ok
                error
                errorCode
              }
            }
            """,
            {"sqid": account.sqid},
            user=user,
        )
    )

    assert data["unlinkAccount"] == {
        "ok": False,
        "error": "only_sign_in_method",
        "errorCode": "only_sign_in_method",
    }
    with system_context(reason="test assertions"):
        owner = ExternalAccount.objects.owner_for(account)
        assert owner is not None
        assert owner.pk == user.pk
        assert Credential.objects.filter(user=user, external_account=account).exists()


@pytest.fixture()
def iam_connection_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete connection tables for IAM GraphQL tests.

    Also materializes any other concrete ``auth``-app tables (e.g. test models
    registered by sibling suites with ``app_label="auth"``): deleting a real
    ``auth.User`` walks Django's deletion collector across every ``auth``-app
    relation (``AuditMixin`` adds ``SET_NULL`` user FKs), so a phantom registered
    model without a table would break ``deleteUser`` under suite ordering.
    """

    del transactional_db
    created_models = _create_connection_tables()
    created_models += _create_auth_app_tables()
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _create_auth_app_tables() -> list[Any]:
    """Create missing tables for concrete managed models in the ``auth`` app."""

    auth_models = tuple(
        model
        for model in apps.get_app_config("auth").get_models()
        if model._meta.managed and not model._meta.abstract
    )
    return _create_connection_tables(auth_models)


def test_discover_oidc_endpoints_is_admin_gated_and_validates_discovery_url(
    iam_connection_tables: None,
) -> None:
    """Discover is admin-gated and reports when no discovery URL is configured."""

    plain = User.objects.create_user(
        username="discover-plain", email="discover-plain@example.com"
    )
    admin = _platform_admin("discover-admin")
    client = _oauth_client("discoverable", discovery_url="")
    client_id = relay.to_base64("OAuthClientType", client.sqid)
    console_schema = _schema("console")
    discover = "mutation($id: ID!){ discoverOidcEndpoints(id: $id){ ok message } }"

    assert _execute(console_schema, discover, {"id": client_id}, user=plain).errors is not None

    result = _data(
        _execute(console_schema, discover, {"id": client_id}, user=admin)
    )["discoverOidcEndpoints"]
    assert result["ok"] is False
    assert "discovery url" in result["message"].lower()


def _schema(name: str) -> Any:
    """Build one IAM-only GraphQL schema bucket."""

    return addon_schema(iam_schema.schemas, name)


def _execute(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a session-bearing request context."""

    return execute_schema(
        schema,
        query,
        variables,
        request=request or _request(user or AnonymousUser()),
    )


def _sdl_block(sdl: str, header: str) -> str:
    """Return one SDL block by its header prefix."""

    start = sdl.index(header)
    body = sdl.index("{", start)
    end = sdl.index("\n}", body)
    return sdl[start:end]


def _request(user: Any) -> Any:
    """Return a request object with a minimal mutable session."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    request.session = _Session()
    return request


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the platform-admin role tuple."""

    admin = User.objects.create_superuser(
        username=username,
        email=f"{username}@example.com",
        password="admin",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _user_global_id(user: Any) -> str:
    """Return the relay global id ``UserType`` mutations resolve for ``user``.

    The source-addon test user is Django's default auth user (no ``sqid``), so its
    public id is the primary key — the same value ``instance_from_public_id`` reads
    back from the global id on the write path.
    """

    return relay.to_base64("UserType", str(getattr(user, "sqid", user.pk)))


def _oauth_client(
    slug: str,
    **oauth_client_overrides: Any,
) -> OAuthClient:
    """Create one self-describing OAuth client under system context."""

    defaults: dict[str, Any] = {
        "display_name": slug.title(),
        "icon": f"{slug}.svg",
        "client_id": f"{slug}-client",
        "client_secret": "secret",
        "issuer": "https://issuer.example",
        "discovery_url": "https://issuer.example/.well-known/openid-configuration",
        "authorize_endpoint": "https://issuer.example/authorize",
        "token_endpoint": "https://issuer.example/token",
        "userinfo_endpoint": "https://issuer.example/userinfo",
        "jwks_uri": "https://issuer.example/jwks",
        "is_oidc": True,
        "is_enabled": True,
        "supports_pkce": False,
        "default_scopes": ["openid", "email"],
    }
    defaults.update(oauth_client_overrides)
    with system_context(reason="test iam graphql setup"):
        return OAuthClient.objects.create(slug=slug, **defaults)


class _Session(dict[str, Any]):
    """Minimal session object for direct GraphQL execution."""

    modified = False

    def cycle_key(self) -> None:
        """Mark the fake session as cycled."""

        self.modified = True

    def flush(self) -> None:
        """Clear the fake session."""

        self.clear()
        self.modified = True
