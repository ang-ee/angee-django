"""Tests for the OAuth/OIDC connection and login GraphQL surfaces.

After the federation split the connection substrate (OAuth/OIDC clients, external
accounts, credentials, connect/disconnect) is owned by ``integrate`` and OIDC
*login* by ``iam_integrate_oidc``; ``iam`` keeps password login + user/permission
admin. These tests compose the three addons' ``schemas`` into one schema per
bucket, the way the runtime composer does.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext, override_settings
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.base.models import (
    instance_from_public_id,
    public_data_id_field,
    public_id_for,
    public_id_of,
)
from angee.graphql.data.field_classification import resource_field_kind, resource_field_widget
from angee.graphql.data.metadata import model_resource_fields
from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import OAuthFlowError
from tests.conftest import (
    SOCIAL_TEST_MODELS,
    Credential,
    ExternalAccount,
    OAuthClient,
    _clear_model_tables,
    addon_schema,
    execute_schema,
)
from tests.conftest import _create_missing_tables as _create_connection_tables
from tests.conftest import result_data as _data

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")
oidc_schema = importlib.import_module("angee.iam_integrate_oidc.schema")
oidc_identity = importlib.import_module("angee.iam_integrate_oidc.identity")
integrate_connect = importlib.import_module("angee.integrate.connect")

# The connection/login surface now spans three addons; tests compose them the way
# the runtime does (one merged schema per bucket).
_SCHEMA_SOURCES = (iam_schema.schemas, integrate_schema.schemas, oidc_schema.schemas)


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
              available_connections(pagination: {limit: 10}) {
                total_count
                results {
                  oauth_client_sqid
                  oauth_client_display_name
                  oauth_client_slug
                  oauth_client_icon
                }
              }
            }
            """,
        )
    )

    connections = data["available_connections"]["results"]
    assert data["available_connections"]["total_count"] == 1
    assert [row["oauth_client_slug"] for row in connections] == ["enabled"]
    assert "clientSecret" not in public_schema.as_str()


def test_available_connections_reads_client_columns_without_per_row_queries(
    iam_connection_tables: None,
) -> None:
    """The picker reads the self-describing client's own columns, so its query count stays flat as rows grow."""

    query = """
        query {
          available_connections(pagination: {limit: 10}) {
            total_count
            results { oauth_client_sqid oauth_client_slug oauth_client_display_name oauth_client_icon }
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

    assert data["available_connections"]["total_count"] == 3
    assert len(three_rows.captured_queries) == len(one_row.captured_queries)


def test_login_start_rejects_non_oidc_or_disabled_oauth_client(
    iam_connection_tables: None,
) -> None:
    """OIDC start fails closed (typed error payload) when the client cannot run login."""

    non_oidc = _oauth_client("oauth", is_oidc=False, is_enabled=True)
    disabled = _oauth_client("off", is_oidc=True, is_enabled=False)
    public_schema = _schema("public")
    query = """
        mutation LoginStart($oauthClientSqid: String!) {
          login_start(
            oauth_client_sqid: $oauthClientSqid,
            redirect_uri: "https://app.example/callback"
          ) {
            state
            error
            error_code
          }
        }
    """

    for oauth_client in (non_oidc, disabled):
        data = _data(_execute(public_schema, query, {"oauthClientSqid": oauth_client.sqid}))

        assert data["login_start"]["state"] == ""
        assert "enabled for OIDC" in data["login_start"]["error"]
        assert data["login_start"]["error_code"] == "client_not_configured"


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
              login_start(
                oauth_client_sqid: $oauthClientSqid,
                redirect_uri: "https://app.example/callback"
              ) {
                authorize_url
                state
                error
                error_code
              }
            }
            """,
            {"oauthClientSqid": oauth_client.sqid},
        )
    )

    assert data["login_start"] == {
        "authorize_url": "",
        "state": "",
        "error": "missing_endpoint",
        "error_code": "missing_endpoint",
    }


@override_settings(
    AUTHENTICATION_BACKENDS=(
        "rebac.backends.auth.RebacBackend",
        "angee.iam.auth.ModelBackend",
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
              login_start(
                oauth_client_sqid: "%s",
                redirect_uri: "https://app.example/callback",
                next: "/after-login"
              ) {
                authorize_url
                state
              }
            }
            """
            % oauth_client.sqid,
            request=request,
        )
    )["login_start"]

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
        return oidc_identity.LoginCompletion(
            user=user,
            claims={"sub": "sub-login", "email": "oidc@example.com"},
            next_path="/after-login",
        )

    monkeypatch.setattr(oidc_identity, "complete_login", complete_login)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              login_complete(
                code: "code",
                state: $state,
                redirect_uri: "https://app.example/callback"
              ) {
                ok
                intent
                next
                claims
                error
                error_code
                user { username }
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert completed["login_complete"] == {
        "ok": True,
        "intent": "login",
        "next": "/after-login",
        "claims": {"sub": "sub-login", "email": "oidc@example.com"},
        "error": None,
        "error_code": None,
        "user": {"username": "oidc-user"},
    }
    assert request.session[SESSION_KEY] == str(user.pk)
    assert request.session[BACKEND_SESSION_KEY] == "angee.iam.auth.ModelBackend"


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
              login_start(
                oauth_client_sqid: "%s",
                redirect_uri: "https://app.example/callback"
              ) {
                state
              }
            }
            """
            % oauth_client.sqid,
            request=request,
        )
    )["login_start"]

    def complete_login(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise OAuthFlowError("invalid_id_token", 400, "bad token")

    monkeypatch.setattr(oidc_identity, "complete_login", complete_login)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              login_complete(
                code: "bad-code",
                state: $state,
                redirect_uri: "https://app.example/callback"
              ) {
                ok
                user { username }
                intent
                next
                claims
                error
                error_code
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert completed["login_complete"] == {
        "ok": False,
        "user": None,
        "intent": "login",
        "next": "/",
        "claims": None,
        "error": "bad token",
        "error_code": "invalid_id_token",
    }


@override_settings(AUTHENTICATION_BACKENDS=("angee.iam.auth.ModelBackend",))
def test_login_upgrades_a_stale_password_hash_for_valid_credentials(
    iam_connection_tables: None,
) -> None:
    """A must_update hash is rehashed on password login though the request is anonymous.

    Django upgrades a stale password hash inside ``check_password`` by saving the
    row (``must_update`` — an iteration bump, salt-entropy policy, or algorithm
    change). Under REBAC fail-closed that save needs an actor the anonymous login
    request does not have, so before ``login`` elevated the credential check the
    save raised ``PermissionDenied``, ``authenticate`` swallowed it as a backend
    refusal, and a user with *valid* credentials was denied. The rehash is a
    system maintenance write the login sanctions, so it must succeed and persist.
    """

    hasher = PBKDF2PasswordHasher()
    raw_password = "correct-horse-battery"
    stale_hash = hasher.encode(raw_password, hasher.salt(), iterations=1000)
    assert hasher.must_update(stale_hash)  # low iterations force an upgrade

    user = _user_with_password_hash("rehash-user", stale_hash)

    data = _data(
        _execute(
            _schema("public"),
            _LOGIN_MUTATION,
            {"username": "rehash-user", "password": raw_password},
            request=_request(AnonymousUser()),
        )
    )

    assert data["login"] == {"ok": True, "user": {"username": "rehash-user"}}
    with system_context(reason="test.iam.login.assert_upgrade"):
        user.refresh_from_db()
    # The stored hash was rehashed to the current policy and still verifies the
    # same credential.
    assert user.password != stale_hash
    assert not hasher.must_update(user.password)
    assert hasher.decode(user.password)["iterations"] == hasher.iterations
    assert hasher.verify(raw_password, user.password)


@override_settings(AUTHENTICATION_BACKENDS=("angee.iam.auth.ModelBackend",))
def test_login_with_wrong_password_fails_and_writes_no_hash_upgrade(
    iam_connection_tables: None,
) -> None:
    """A wrong password is refused and never triggers the rehash write."""

    hasher = PBKDF2PasswordHasher()
    stale_hash = hasher.encode("right-password", hasher.salt(), iterations=1000)
    user = _user_with_password_hash("no-rehash-user", stale_hash)

    data = _data(
        _execute(
            _schema("public"),
            _LOGIN_MUTATION,
            {"username": "no-rehash-user", "password": "wrong-password"},
            request=_request(AnonymousUser()),
        )
    )

    assert data["login"] == {"ok": False, "user": None}
    with system_context(reason="test.iam.login.assert_no_upgrade"):
        user.refresh_from_db()
    assert user.password == stale_hash


@override_settings(AUTHENTICATION_BACKENDS=("angee.iam.auth.ModelBackend",))
def test_login_refuses_service_account(
    iam_connection_tables: None,
) -> None:
    """Service users are non-login principals, so password login fails natively."""

    user = User.objects.create_user(
        username="svc-agent",
        email="svc-agent@example.com",
        password="secret",
        kind="service",
    )

    data = _data(
        _execute(
            _schema("public"),
            _LOGIN_MUTATION,
            {"username": "svc-agent", "password": "secret"},
            request=_request(AnonymousUser()),
        )
    )

    assert data["login"] == {"ok": False, "user": None}
    with system_context(reason="test.iam.login.service_user"):
        user.refresh_from_db()
    assert not user.has_usable_password()


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
              link_account_start(
                oauth_client_sqid: "%s",
                redirect_uri: "https://app.example/callback",
                next: "https://evil.example/phish"
              ) {
                state
              }
            }
            """
            % oauth_client.sqid,
            request=request,
        )
    )["link_account_start"]

    def complete_link(
        selected_oauth_client: OAuthClient,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> Any:
        assert selected_oauth_client.pk == oauth_client.pk
        assert code == "code"
        assert redirect_uri == "https://app.example/callback"
        record = state.consume(state_token)
        assert record.next_path == "/"
        return oidc_identity.LinkCompletion(
            account=account,
            user=user,
            claims={"sub": "sub-link-rich", "email": "link@example.com"},
            next_path=record.next_path or "/",
        )

    monkeypatch.setattr(oidc_identity, "complete_link", complete_link)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              link_account_complete(
                code: "code",
                state: $state,
                redirect_uri: "https://app.example/callback"
              ) {
                account { external_id }
                user { username }
                intent
                next
                claims
                error
                error_code
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert completed["link_account_complete"] == {
        "account": {"external_id": "sub-link-rich"},
        "user": {"username": "link-user"},
        "intent": "link",
        "next": "/",
        "claims": {"sub": "sub-link-rich", "email": "link@example.com"},
        "error": None,
        "error_code": None,
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
    oauth_client_id = str(oauth_client.sqid)
    start = _data(
        _execute(
            public_schema,
            """
            mutation Start($id: ID!) {
              connect_account_start(
                id: $id,
                redirect_uri: "https://app.example/callback"
              ) {
                state
                error
                error_code
              }
            }
            """,
            {"id": oauth_client_id},
            request=request,
        )
    )["connect_account_start"]

    def complete_account_connect(
        selected_oauth_client: OAuthClient,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> Any:
        del code, state_token, redirect_uri
        assert selected_oauth_client.pk == oauth_client.pk
        raise OAuthFlowError(
            "token_exchange_failed",
            429,
            body={
                "error": {
                    "type": "rate_limit_error",
                    "message": "Rate limited. Please try again later.",
                },
            },
        )

    monkeypatch.setattr(integrate_connect, "complete_account_connect", complete_account_connect)

    completed = _data(
        _execute(
            public_schema,
            """
            mutation Complete($state: String!) {
              connect_account_complete(
                code: "code",
                state: $state,
                redirect_uri: "https://app.example/callback"
              ) {
                account { external_id }
                credential { display_name }
                error
                error_code
              }
            }
            """,
            {"state": start["state"]},
            request=request,
        )
    )

    assert start["error"] is None
    assert completed["connect_account_complete"] == {
        "account": None,
        "credential": None,
        "error": "Rate limited. Please try again later.",
        "error_code": "token_exchange_failed",
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
          insert_oauth_clients_one(object: {
            slug: "console",
            icon: "console.svg",
            display_name: "Console prod",
            client_id: "console-client",
            client_secret: "console-secret",
            is_enabled: true,
            authorize_endpoint: "https://issuer.example/authorize",
            token_endpoint: "https://issuer.example/token"
          }) {
            id
            slug
            icon
            display_name
            configuration_state
          }
        }
    """

    denied = _execute(console_schema, create_oauth_client, user=user)
    assert denied.errors is not None

    oauth_client = _data(
        _execute(console_schema, create_oauth_client, user=admin)
    )["insert_oauth_clients_one"]
    oauth_client_id = oauth_client["id"]
    assert oauth_client["slug"] == "console"
    assert oauth_client["icon"] == "console.svg"
    assert oauth_client["display_name"] == "Console prod"
    assert oauth_client["configuration_state"] == "ready"
    with system_context(reason="test.iam.oauth_client_secret"):
        stored_client = OAuthClient.objects.get(client_id="console-client")
    assert stored_client.client_secret == "console-secret"
    revealed = _data(
        _execute(
            console_schema,
            """
            mutation Reveal($id: ID!) {
              reveal_oauth_client_secret(id: $id) { secret }
            }
            """,
            variables={"id": oauth_client_id},
            user=admin,
        )
    )["reveal_oauth_client_secret"]
    assert revealed == {"secret": "console-secret"}

    external_account_mutation = """
        mutation CreateExternalAccount($oauthClient: ID!, $owner: String!) {
          create_external_account(data: {
            oauth_client: $oauthClient,
            owner: $owner,
            external_id: "admin-sub",
            email: "admin@example.com",
            display_name: "Admin OIDC",
            status: "active"
          }) {
            id
            external_id
            email
            display_name
            status
            provider_slug
          }
        }
    """
    linked = _data(
        _execute(
            console_schema,
            external_account_mutation,
            {"oauthClient": oauth_client_id, "owner": _user_public_id(admin)},
            user=admin,
        )
    )["create_external_account"]
    assert linked["external_id"] == "admin-sub"
    assert linked["email"] == "admin@example.com"
    assert linked["provider_slug"] == "console"
    with system_context(reason="test.iam.external_account"):
        account = ExternalAccount.objects.get(external_id="admin-sub")
    assert ExternalAccount.objects.owner_for(account) == admin
    assert _execute(
        console_schema,
        external_account_mutation,
        {"oauthClient": oauth_client_id, "owner": _user_public_id(admin)},
        user=user,
    ).errors is not None


def test_oauth_client_crud_sets_oidc_login_fields(
    iam_connection_tables: None,
) -> None:
    """OIDC login fields are updated on the OAuth client row and admin gated."""

    plain = User.objects.create_user(username="oidc-crud-plain", email="plain@example.com")
    admin = _platform_admin("oidc-crud-admin")
    oauth_client = _oauth_client("refine-me", is_oidc=False)
    oauth_client_id = str(oauth_client.sqid)
    console_schema = _schema("console")
    update_oauth_client = """
        mutation UpdateOauthClient($id: String!) {
          update_oauth_clients_by_pk(
            pk_columns: {id: $id},
            _set: {
            issuer: "https://issuer.example",
            discovery_url: "https://issuer.example/.well-known/openid-configuration",
            login_enabled: true,
            create_on_login: true,
            allowed_email_domains: ["example.com"]
          }) {
            issuer
            discovery_url
            login_enabled
            create_on_login
            allowed_email_domains
          }
        }
    """
    variables = {"id": oauth_client_id}

    assert _execute(console_schema, update_oauth_client, variables, user=plain).errors is not None

    updated = _data(
        _execute(console_schema, update_oauth_client, variables, user=admin)
    )["update_oauth_clients_by_pk"]
    assert updated == {
        "issuer": "https://issuer.example",
        "discovery_url": "https://issuer.example/.well-known/openid-configuration",
        "login_enabled": True,
        "create_on_login": True,
        "allowed_email_domains": ["example.com"],
    }
    with system_context(reason="test.iam_integrate_oidc.oauth_client_oidc"):
        oauth_client.refresh_from_db()
        assert oauth_client.login_enabled is True
        assert oauth_client.create_on_login is True


def test_reveal_credential_returns_the_secret_and_is_admin_only(
    iam_connection_tables: None,
) -> None:
    """Reveal decrypts the stored secret for an admin only; the read type never carries it."""

    plain = User.objects.create_user(username="reveal-plain", email="plain@example.com")
    admin = _platform_admin("reveal-admin")
    console_schema = _schema("console")

    create_credential = """
        mutation CreateCredential {
          create_credential(data: {name: "GitHub PAT", kind: "static_token", api_key: "ghp_secret_value"}) {
            id
          }
        }
    """
    credential_id = _data(_execute(console_schema, create_credential, user=admin))["create_credential"]["id"]

    reveal = "mutation Reveal($id: ID!) { reveal_credential(id: $id) { secret } }"
    revealed = _data(_execute(console_schema, reveal, {"id": credential_id}, user=admin))["reveal_credential"]
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
          insert_users_one(object: {
            username: "console-user",
            password: "first-secret",
            email: "console-user@example.com",
            first_name: "Console",
            last_name: "User",
            is_staff: true,
            is_active: true
          }) {
            username
            email
            first_name
            last_name
            is_staff
            is_active
            full_name
          }
        }
    """

    assert _execute(console_schema, create_user, user=plain).errors is not None

    created = _data(_execute(console_schema, create_user, user=admin))["insert_users_one"]
    assert created == {
        "username": "console-user",
        "email": "console-user@example.com",
        "first_name": "Console",
        "last_name": "User",
        "is_staff": True,
        "is_active": True,
        "full_name": "Console User",
    }
    # ``password`` is write-only: it is neither a field on ``UserType`` nor in its SDL.
    assert "password" not in _sdl_block(console_schema.as_str(), "type UserType")
    with system_context(reason="test.iam.user_crud.create"):
        user = User.objects.get(username="console-user")
        assert user.check_password("first-secret")
    user_id = _user_public_id(user)

    changed = _data(
        _execute(
            console_schema,
            """
            mutation UpdateUser($id: String!) {
              update_users_by_pk(
                pk_columns: {id: $id},
                _set: {first_name: "Renamed", is_staff: false}
              ) {
                first_name
                is_staff
              }
            }
            """,
            {"id": user_id},
            user=admin,
        )
    )["update_users_by_pk"]
    assert changed == {"first_name": "Renamed", "is_staff": False}
    with system_context(reason="test.iam.user_crud.update_field"):
        user.refresh_from_db()
        assert user.first_name == "Renamed"
        assert user.is_staff is False
        assert user.check_password("first-secret")

    assert _execute(
        console_schema,
        """
        mutation InvalidEmail($id: String!) {
          update_users_by_pk(
            pk_columns: {id: $id},
            _set: {email: "not-an-email"}
          ) {
            email
          }
        }
        """,
        {"id": user_id},
        user=admin,
    ).errors is not None
    with system_context(reason="test.iam.user_crud.invalid_email"):
        user.refresh_from_db()
        assert user.email == "console-user@example.com"

    _data(
        _execute(
            console_schema,
            """
            mutation RehashUser($id: String!) {
              update_users_by_pk(
                pk_columns: {id: $id},
                _set: {password: "second-secret"}
              ) {
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
          delete_user(id: $id, confirm: true) { total_deleted_count }
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
              delete_user(id: $id, confirm: true) {
                has_blockers
                total_deleted_count
              }
            }
            """,
            {"id": user_id},
            user=admin,
        )
    )["delete_user"]
    assert deleted["has_blockers"] is False
    assert deleted["total_deleted_count"] >= 1
    with system_context(reason="test.iam.user_crud.delete"):
        assert not User.objects.filter(pk=user.pk).exists()


def test_current_user_preferences_default_to_empty_object(
    iam_connection_tables: None,
) -> None:
    """User preference projections return an object even before a user customizes UI."""

    user = User.objects.create_user(
        username="prefs-user",
        email="prefs-user@example.com",
    )
    public_schema = _schema("public")

    data = _data(
        _execute(
            public_schema,
            """
            query {
              current_user {
                username
                preferences
              }
            }
            """,
            user=user,
        )
    )

    assert data["current_user"] == {
        "username": "prefs-user",
        "preferences": {},
    }
    assert "preferences" in _sdl_block(public_schema.as_str(), "type UserType")
    assert "preferences" in _sdl_block(
        public_schema.as_str(),
        "type CurrentUserType",
    )


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
    account_id = str(account.sqid)
    console_schema = _schema("console")
    update_account = """
        mutation UpdateExternalAccount($id: String!) {
          update_external_accounts_by_pk(
            pk_columns: {id: $id},
            _set: {
            email: "after@example.com",
            display_name: "After",
            status: "revoked"
          }) {
            email
            display_name
            status
          }
        }
    """

    assert _execute(console_schema, update_account, {"id": account_id}, user=plain).errors is not None

    updated = _data(
        _execute(console_schema, update_account, {"id": account_id}, user=admin)
    )["update_external_accounts_by_pk"]
    # ``status`` is a choices field exposed as a GraphQL enum, so it renders as the
    # uppercase member name though the write input takes the raw ``"revoked"`` value.
    assert updated == {
        "email": "after@example.com",
        "display_name": "After",
        "status": "REVOKED",
    }
    with system_context(reason="test.iam.external_account.owner_before_delete"):
        assert ExternalAccount.objects.owner_for(account) == admin

    delete_account = """
        mutation DeleteExternalAccount($id: ID!) {
          delete_external_account(id: $id, confirm: true) {
            has_blockers
            total_deleted_count
          }
        }
    """

    assert _execute(console_schema, delete_account, {"id": account_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_account, {"id": account_id}, user=admin)
    )["delete_external_account"]
    assert deleted["has_blockers"] is False
    assert deleted["total_deleted_count"] >= 1
    with system_context(reason="test.iam.external_account.after_delete"):
        assert not ExternalAccount.objects.filter(pk=account.pk).exists()
        assert ExternalAccount.objects.owner_for(account) is None


def test_credential_crud_create_delete_are_admin_only(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating a static-token credential renders ``display_name``; delete is admin gated."""

    plain = User.objects.create_user(username="cred-crud-plain", email="plain@example.com")
    admin = _platform_admin("cred-crud-admin")
    owner = User.objects.create_user(username="cred-crud-owner", email="owner@example.com")
    console_schema = _schema("console")
    create_credential = """
        mutation CreateCredential($user: ID!) {
          create_credential(data: {
            user: $user,
            name: "ci-token",
            kind: "static_token",
            api_key: "static-token-value"
          }) {
            kind
            name
            status
            display_name
          }
        }
    """
    variables = {"user": _user_public_id(owner)}

    assert _execute(console_schema, create_credential, variables, user=plain).errors is not None

    created = _data(
        _execute(console_schema, create_credential, variables, user=admin)
    )["create_credential"]
    assert created["name"] == "ci-token"
    # A provider-less static token reads its own name as the label.
    assert created["display_name"] == "ci-token"
    # ``api_key`` is write-only: it never surfaces on ``CredentialType``.
    assert "apiKey" not in _sdl_block(console_schema.as_str(), "type CredentialType")
    with system_context(reason="test.iam.credential_crud.create"):
        credential = Credential.objects.get(user=owner, name="ci-token")
        assert credential.kind == CredentialKind.STATIC_TOKEN
        assert credential.oauth_client_id is None
    credential_id = str(credential.sqid)
    scheduled: list[Any] = []
    revoked: list[Any] = []
    monkeypatch.setattr(integrate_schema.transaction, "on_commit", lambda callback: scheduled.append(callback))
    monkeypatch.setattr(
        integrate_schema,
        "_revoke_remote_oauth_token",
        lambda credential: revoked.append(credential.pk),
    )

    delete_credential = """
        mutation DeleteCredential($id: ID!) {
          delete_credential(id: $id, confirm: true) {
            has_blockers
            total_deleted_count
          }
        }
    """

    assert _execute(console_schema, delete_credential, {"id": credential_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_credential, {"id": credential_id}, user=admin)
    )["delete_credential"]
    assert deleted["has_blockers"] is False
    assert deleted["total_deleted_count"] >= 1
    assert revoked == []
    assert len(scheduled) == 1
    scheduled.pop()()
    assert revoked == [credential.pk]
    with system_context(reason="test.iam.credential_crud.delete"):
        assert not Credential.objects.filter(pk=credential.pk).exists()


def test_credential_health_display_name_query_count_stays_flat(
    iam_connection_tables: None,
) -> None:
    """Credential display labels carry their own optimizer hints."""

    admin = _platform_admin("cred-label-admin")
    console_schema = _schema("console")
    query = """
        query {
          credentials(limit: 10) { display_name }
        }
    """

    oauth_client = _oauth_client("cred-label-provider")
    owner = User.objects.create_user(username="cred-label-one", email="one@example.com")
    account = ExternalAccount.objects.link(oauth_client, "cred-label-one", owner=owner, email="one@example.com")
    Credential.objects.upsert_for_user(
        owner,
        oauth_client,
        CredentialKind.STATIC_TOKEN,
        {"api_key": "one"},
        external_account=account,
    )
    with CaptureQueriesContext(connection) as one_row:
        _data(_execute(console_schema, query, user=admin))

    for index in range(2, 4):
        owner = User.objects.create_user(
            username=f"cred-label-{index}",
            email=f"{index}@example.com",
        )
        account = ExternalAccount.objects.link(
            oauth_client,
            f"cred-label-{index}",
            owner=owner,
            email=f"{index}@example.com",
        )
        Credential.objects.upsert_for_user(
            owner,
            oauth_client,
            CredentialKind.STATIC_TOKEN,
            {"api_key": str(index)},
            external_account=account,
        )
    with CaptureQueriesContext(connection) as three_rows:
        data = _data(_execute(console_schema, query, user=admin))

    assert len(data["credentials"]) == 3
    assert len(three_rows.captured_queries) == len(one_row.captured_queries)


def test_console_external_accounts_render_provider_projection(
    iam_connection_tables: None,
) -> None:
    """The admin external_accounts list renders provider_* through the guarded join.

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
              external_accounts {
                external_id
                provider_slug
                provider_environment
                provider_label
              }
            }
            """,
            user=admin,
        )
    )["external_accounts"]
    row = next(item for item in accounts if item["external_id"] == "list-sub")
    assert row["provider_slug"] == "listco"
    assert row["provider_environment"] == "prod"
    assert row["provider_label"] == "ListCo prod"


def test_console_external_accounts_provider_projection_query_count_stays_flat(
    iam_connection_tables: None,
) -> None:
    """The console external-account queryset owns the guarded OAuth-client load contract."""

    admin = _platform_admin("ea-list-count-admin")
    console_schema = _schema("console")
    query = """
        query {
          external_accounts(limit: 10) {
            external_id
            provider_slug
            provider_environment
            provider_label
            provider_icon
          }
        }
    """

    ExternalAccount.objects.link(_oauth_client("ea-one"), "sub-one", owner=admin)
    with CaptureQueriesContext(connection) as one_row:
        _data(_execute(console_schema, query, user=admin))

    ExternalAccount.objects.link(_oauth_client("ea-two"), "sub-two", owner=admin)
    ExternalAccount.objects.link(_oauth_client("ea-three"), "sub-three", owner=admin)
    with CaptureQueriesContext(connection) as three_rows:
        data = _data(_execute(console_schema, query, user=admin))

    assert len(data["external_accounts"]) == 3
    assert len(three_rows.captured_queries) == len(one_row.captured_queries)


def test_oauth_client_secret_uses_reveal_posture(
    iam_connection_tables: None,
) -> None:
    """OAuth client secrets are writable but revealed only by an admin action."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "clientSecret" not in public_sdl
    assert "client_secret" not in _sdl_block(console_sdl, "type OAuthClientType")
    assert "reveal_oauth_client_secret" in _sdl_block(console_sdl, "type Mutation")
    assert "client_secret" in _sdl_block(console_sdl, "input oauth_clients_insert_input")
    assert "client_secret" in _sdl_block(console_sdl, "input oauth_clients_set_input")
    for sdl in (public_sdl, console_sdl):
        assert "material" not in sdl
        assert "identityClaims" not in sdl


def test_account_connect_schema_exposes_generic_flow_without_token_material(
    iam_connection_tables: None,
) -> None:
    """The connection surface exposes account-connect mutations and OAuth metadata without token values."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "connect_account_start(" in _sdl_block(public_sdl, "type Mutation")
    assert "connect_account_complete(" in _sdl_block(public_sdl, "type Mutation")
    assert "credential: ConnectedCredentialType" in _sdl_block(public_sdl, "type ConnectAccountResult")
    assert "type CredentialType implements" not in public_sdl
    assert "provider_slug" not in public_sdl
    assert "oauth_client:" not in public_sdl
    oauth_client_type = _sdl_block(console_sdl, "type OAuthClientType")
    oauth_client_insert = _sdl_block(console_sdl, "input oauth_clients_insert_input")
    oauth_client_set = _sdl_block(console_sdl, "input oauth_clients_set_input")
    for field in ("authorize_params", "token_params", "token_request_format", "external_id_claim", "email_claim"):
        assert field in oauth_client_type
        assert field in oauth_client_insert
        assert field in oauth_client_set
    oauth_client_oidc_extension = _sdl_block(console_sdl, "extend type OAuthClientType")
    for field in ("jwks_uri", "login_enabled", "link_on_email_match", "create_on_login"):
        assert field in oauth_client_oidc_extension
        assert field in oauth_client_insert
        assert field in oauth_client_set
    assert "access_token" not in public_sdl
    assert "refresh_token" not in public_sdl


def test_oauth_client_resource_metadata_includes_oidc_extension_fields(
    iam_connection_tables: None,
) -> None:
    """Same-row OIDC extension fields keep their model-owned resource metadata."""

    console_schema = _schema("console")
    metadata = {item.model_label: item for item in console_schema.angee_resources}
    oauth_client = metadata["integrate.OAuthClient"]
    fields = {field.name: field for field in oauth_client.fields}

    assert oauth_client.create_fields[-6:] == (
        "issuer",
        "jwks_uri",
        "login_enabled",
        "link_on_email_match",
        "create_on_login",
        "allowed_email_domains",
    )
    assert fields["issuer"].scalar == "String"
    assert fields["jwks_uri"].scalar == "String"
    for name in ("login_enabled", "link_on_email_match", "create_on_login"):
        assert fields[name].scalar == "Boolean"
        assert fields[name].widget == "switch"
        assert fields[name].creatable is True
        assert fields[name].updatable is True
    assert fields["allowed_email_domains"].scalar == "JSON"
    assert fields["allowed_email_domains"].widget == "json"


def test_model_resource_fields_rejects_enum_declared_field() -> None:
    """A declared enum column fails fast: its values are owned by the node surface."""

    with pytest.raises(ImproperlyConfigured, match="cannot reconstruct enum"):
        model_resource_fields(ExternalAccount, ("status",))


def test_model_resource_fields_reconstructs_relation_target_label() -> None:
    """A declared same-row relation keeps its target label, resolved from the model."""

    (field,) = model_resource_fields(ExternalAccount, ("oauth_client",))
    assert field.kind == "relation"
    assert field.scalar is None
    assert field.relation_model_label == OAuthClient._meta.label


def test_scalar_id_to_one_relation_classifies_as_leaf() -> None:
    """An FK a node projects as a bare ``ID`` scalar is a scalar leaf, not an object.

    A to-one FK projected as an object stays a ``relation`` (an object selection, a
    ``many2one`` picker). Projected as a bare ``ID`` scalar it must classify as a
    ``scalar`` leaf so the detail/form query selects it without an invalid
    sub-selection — while still resolving a scalar-id ``select`` picker widget.
    """

    oauth_client_fk = ExternalAccount._meta.get_field("oauth_client")

    # Object projection: object relation, many2one picker.
    assert resource_field_kind(oauth_client_fk, is_object=True) == "relation"
    assert resource_field_widget(oauth_client_fk, "relation") == "many2one"

    # Bare-ID-scalar projection: scalar leaf carrying the scalar-id select widget.
    scalar_kind = resource_field_kind(oauth_client_fk, projected_as_scalar=True)
    assert scalar_kind == "scalar"
    assert resource_field_widget(oauth_client_fk, scalar_kind) == "select"


def test_scalar_id_relation_axis_classifies_as_leaf() -> None:
    """A scalar-id FK stays a leaf even when it also contributes a group axis."""

    oauth_client_fk = ExternalAccount._meta.get_field("oauth_client")

    kind = resource_field_kind(
        oauth_client_fk,
        has_relation_axis=True,
        projected_as_scalar=True,
    )

    assert kind == "scalar"
    assert resource_field_widget(oauth_client_fk, kind) == "select"


def test_console_schema_exposes_user_change_subscription(
    iam_connection_tables: None,
) -> None:
    """The IAM users view has a console change stream to subscribe to."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "userChanged" not in public_sdl
    assert "userChanged: ChangeEvent!" in _sdl_block(console_sdl, "type Subscription")


@pytest.mark.django_db
def test_iam_group_public_identity_is_sqid_addressable() -> None:
    """The IAM auth-group data surface satisfies the public identity contract."""

    group = iam_schema.Group.objects.create(name="Operators")
    group_id = iam_schema.GROUP_PUBLIC_IDENTITY.public_id_from_pk(group.pk)

    assert public_data_id_field(iam_schema.Group) is None
    assert public_id_of(group) == str(group.pk)
    assert group_id.startswith("grp_")
    assert public_id_for(iam_schema.Group, group.pk, public_identity=iam_schema.GROUP_PUBLIC_IDENTITY) == group_id
    assert (
        instance_from_public_id(
            iam_schema.Group,
            group_id,
            public_identity=iam_schema.GROUP_PUBLIC_IDENTITY,
        ).pk
        == group.pk
    )


@pytest.mark.django_db
def test_iam_group_hasura_resource_uses_public_identity_sqids() -> None:
    """The IAM auth-group catalogue surfaces list and detail rows by public sqids."""

    group = iam_schema.Group.objects.create(name="Operators")
    group_id = iam_schema.GROUP_PUBLIC_IDENTITY.public_id_from_pk(group.pk)
    call_command("rebac", "sync", verbosity=0)
    admin = _platform_admin("auth-catalogue-admin")
    console_schema = _schema("console")

    metadata = {item.model_label: item for item in console_schema.angee_resources}
    assert metadata["iam.Group"].roots.list_name == "groups"
    assert metadata["iam.Group"].roots.detail_name == "groups_by_pk"
    assert "iam.Permission" not in metadata

    data = _data(
        _execute(
            console_schema,
            """
            query AuthCatalogue($groupId: String!) {
              groups(limit: 10) { id name }
              groups_by_pk(id: $groupId) { id name }
            }
            """,
            {"groupId": group_id},
            user=admin,
        )
    )

    assert {"id": group_id, "name": "Operators"} in data["groups"]
    assert data["groups_by_pk"] == {"id": group_id, "name": "Operators"}


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
              my_connected_accounts(pagination: {limit: 10}) {
                results {
                  display_name
                  status
                  external_account { external_id email credential_status }
                }
              }
            }
            """,
            user=alice,
        )
    )

    accounts = data["my_connected_accounts"]["results"]
    assert [row["external_account"]["external_id"] for row in accounts] == ["alice-ext"]
    assert accounts[0]["display_name"] == "Scope (alice@vendor.example)"
    assert accounts[0]["external_account"]["email"] == "alice@vendor.example"
    assert accounts[0]["external_account"]["credential_status"] == "active"

    rich_account = _execute(
        _schema("public"),
        """
        query {
          my_connected_accounts(pagination: {limit: 10}) {
            results {
              oauth_client { display_name }
              external_account { provider_slug }
            }
          }
        }
        """,
        user=alice,
    )
    assert rich_account.errors is not None


def test_disconnect_account_only_removes_callers_credential(
    iam_connection_tables: None,
) -> None:
    """Disconnecting deletes the session user's credential and leaves others alone."""

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
            mutation Disconnect($sqid: String!) {
              disconnect_account(external_account_sqid: $sqid) {
                ok
                error
                error_code
              }
            }
            """,
            {"sqid": account.sqid},
            user=alice,
        )
    )

    assert data["disconnect_account"] == {"ok": True, "error": None, "error_code": None}
    with system_context(reason="test assertions"):
        assert not Credential.objects.filter(user=alice, external_account=account).exists()
        assert Credential.objects.filter(user=bob, external_account=account).exists()


def test_disconnect_account_docstring_names_check_disconnect_owner() -> None:
    """The disconnect mutation docs must describe the current guard owner."""

    doc = integrate_schema.ConnectionMutation.disconnect_account.__doc__ or ""

    assert "pre_delete" not in doc
    assert "check_disconnect" in doc


def test_disconnect_account_revokes_owner_so_oidc_login_is_blocked(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnecting an OIDC credential revokes ownership so future OIDC login fails."""

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

    def revoke_token(self: OAuthClientProtocol, token: str) -> None:
        assert self.oauth_client.pk == oauth_client.pk
        revoked_tokens.append(token)

    monkeypatch.setattr(OAuthClientProtocol, "revoke_token", revoke_token)

    data = _data(
        _execute(
            _schema("public"),
            """
            mutation Disconnect($sqid: String!) {
              disconnect_account(external_account_sqid: $sqid) {
                ok
                error
                error_code
              }
            }
            """,
            {"sqid": account.sqid},
            user=user,
        )
    )

    assert data["disconnect_account"] == {"ok": True, "error": None, "error_code": None}
    assert revoked_tokens == ["unlink-access"]
    with system_context(reason="test assertions"):
        assert ExternalAccount.objects.owner_for(account) is None
        assert not Credential.objects.filter(user=user, external_account=account).exists()
    with pytest.raises(OAuthFlowError):
        oidc_identity.OidcIdentityResolver(oauth_client).resolve(
            sub="sub-unlinked",
            email="owner@example.com",
            claims={"sub": "sub-unlinked"},
        )


def test_disconnect_account_blocks_last_oidc_sign_in_method_for_passwordless_user(
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
            mutation Disconnect($sqid: String!) {
              disconnect_account(external_account_sqid: $sqid) {
                ok
                error
                error_code
              }
            }
            """,
            {"sqid": account.sqid},
            user=user,
        )
    )

    assert data["disconnect_account"] == {
        "ok": False,
        "error": "only_sign_in_method",
        "error_code": "only_sign_in_method",
    }
    with system_context(reason="test assertions"):
        owner = ExternalAccount.objects.owner_for(account)
        assert owner is not None
        assert owner.pk == user.pk
        assert Credential.objects.filter(user=user, external_account=account).exists()


@pytest.fixture()
def iam_connection_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete connection tables for connection/login GraphQL tests.

    Also materializes any other concrete ``auth``-app tables (e.g. test models
    registered by sibling suites with ``app_label="auth"``): deleting a real
    ``auth.User`` walks Django's deletion collector across every ``auth``-app
    relation (``AuditMixin`` adds ``SET_NULL`` user FKs), so a phantom registered
    model without a table would break ``delete_user`` under suite ordering. The
    OIDC last-sign-in disconnect guard is contributed through settings, matching
    composed runtime behavior.
    """

    del transactional_db
    from tests.test_messaging import MESSAGING_TEST_MODELS

    connection_models = MESSAGING_TEST_MODELS + SOCIAL_TEST_MODELS
    _create_connection_tables(connection_models)
    auth_models = tuple(_create_auth_app_tables())
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(connection_models + auth_models)


def _create_auth_app_tables() -> list[Any]:
    """Create missing tables for concrete managed models in the ``auth`` app."""

    auth_models = tuple(
        model
        for model in apps.get_app_config("auth").get_models()
        if model._meta.managed and not model._meta.abstract
    )
    return _create_connection_tables(auth_models)


def test_discover_oauth_endpoints_is_admin_gated_and_validates_discovery_url(
    iam_connection_tables: None,
) -> None:
    """Discover is admin-gated and reports when no discovery URL is configured."""

    plain = User.objects.create_user(
        username="discover-plain", email="discover-plain@example.com"
    )
    admin = _platform_admin("discover-admin")
    client = _oauth_client("discoverable", discovery_url="")
    oauth_client_id = str(client.sqid)
    console_schema = _schema("console")
    discover = "mutation($id: ID!){ discover_oauth_endpoints(id: $id){ ok message } }"

    assert _execute(console_schema, discover, {"id": oauth_client_id}, user=plain).errors is not None

    result = _data(
        _execute(console_schema, discover, {"id": oauth_client_id}, user=admin)
    )["discover_oauth_endpoints"]
    assert result["ok"] is False
    assert "discovery url" in result["message"].lower()


def _schema(name: str) -> Any:
    """Build one merged GraphQL schema bucket spanning iam + integrate + login."""

    merged: dict[str, tuple[Any, ...]] = {}
    for source in _SCHEMA_SOURCES:
        for key, values in source.get(name, {}).items():
            merged[key] = _dedup((*merged.get(key, ()), *values))
    return addon_schema({name: merged}, name)


def _dedup(values: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return ``values`` with duplicate contributions (same object) removed, in order."""

    seen: set[int] = set()
    out: list[Any] = []
    for value in values:
        if id(value) not in seen:
            seen.add(id(value))
            out.append(value)
    return tuple(out)


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


_LOGIN_MUTATION = """
    mutation Login($username: String!, $password: String!) {
      login(username: $username, password: $password) {
        ok
        user { username }
      }
    }
"""


def _user_with_password_hash(username: str, password_hash: str) -> Any:
    """Create a user whose stored password is exactly ``password_hash``."""

    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    with system_context(reason="test.iam.login.seed_hash"):
        user.password = password_hash
        user.save(update_fields=["password"])
    return user


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the platform-admin role tuple."""

    admin = User.objects.create_superuser(
        username=username,
        email=f"{username}@example.com",
        password="admin",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _user_public_id(user: Any) -> str:
    """Return the public id ``UserType`` mutations resolve for ``user``.

    The bare harness uses the concrete IAM user model, so the public id is the
    same sqid that runtime projects expose at the GraphQL boundary.
    """

    return str(getattr(user, "sqid", user.pk))


def _oauth_client(
    slug: str,
    *,
    is_oidc: bool = True,
    is_enabled: bool = True,
    **overrides: Any,
) -> OAuthClient:
    """Create one self-describing OAuth client with optional OIDC login fields."""

    defaults: dict[str, Any] = {
        "display_name": slug.title(),
        "icon": f"{slug}.svg",
        "client_id": f"{slug}-client",
        "client_secret": "secret",
        "discovery_url": "",
        "authorize_endpoint": "https://issuer.example/authorize",
        "token_endpoint": "https://issuer.example/token",
        "userinfo_endpoint": "https://issuer.example/userinfo",
        "is_enabled": is_enabled,
        "supports_pkce": False,
        "default_scopes": ["openid", "email"],
        # Explicit endpoints (above) make this a fully configured login provider; no
        # discovery_url, so the OIDC flow never makes a discovery network call. Tests that
        # exercise discovery set discovery_url explicitly.
        "issuer": "https://issuer.example" if is_oidc else "",
        "jwks_uri": "https://issuer.example/jwks" if is_oidc else "",
        "login_enabled": is_oidc,
        "link_on_email_match": False,
        "create_on_login": False,
        "allowed_email_domains": [],
    }
    defaults.update(overrides)
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
