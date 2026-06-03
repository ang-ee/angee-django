"""Tests for IAM connection GraphQL surfaces."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from rebac import actor_context, system_context
from rebac.roles import grant

from angee.base.apps import SCHEMA_PART_KEYS
from angee.base.graphql.schema import GraphQLSchemas
from angee.iam.credentials import CredentialKind
from angee.iam.signals import PLATFORM_ADMIN_ROLE
from tests.conftest import Credential, ExternalAccount, OAuthClient, Vendor
from tests.conftest import _create_missing_tables as _create_connection_tables

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")


def test_available_connections_returns_only_enabled_oauth_clients_without_secret_fields(
    iam_connection_tables: None,
) -> None:
    """The public picker is system-scoped but only exposes safe enabled rows."""

    _vendor_and_oauth_client("enabled", is_oidc=True, is_enabled=True, client_secret="secret")
    _vendor_and_oauth_client("disabled", is_oidc=True, is_enabled=False, client_secret="secret")
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
                  isOidc
                  vendor { slug displayName icon }
                }
              }
            }
            """,
        )
    )

    connections = data["availableConnections"]["results"]
    assert data["availableConnections"]["totalCount"] == 1
    assert [row["vendor"]["slug"] for row in connections] == ["enabled"]
    assert connections[0]["isOidc"] is True
    assert "clientSecret" not in public_schema.as_str()


def test_login_start_rejects_non_oidc_or_disabled_oauth_client(
    iam_connection_tables: None,
) -> None:
    """OIDC start fails closed when the selected OAuth client cannot run login."""

    _vendor, non_oidc = _vendor_and_oauth_client("oauth", is_oidc=False, is_enabled=True)
    _vendor, disabled = _vendor_and_oauth_client("off", is_oidc=True, is_enabled=False)
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


def test_login_complete_provisions_and_logs_in(
    iam_connection_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OIDC completion delegates identity resolution then writes the session."""

    _vendor, oauth_client = _vendor_and_oauth_client("oidc", is_oidc=True, is_enabled=True)
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
                redirectUri: "https://app.example/callback"
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
        return user

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
        "user": {"username": "oidc-user"},
    }
    assert request.session[SESSION_KEY] == str(user.pk)


def test_vendor_and_oauth_client_crud_are_admin_only(
    iam_connection_tables: None,
) -> None:
    """Console CRUD is denied to non-admins and allowed for platform admins."""

    user = User.objects.create_user(username="plain", email="plain@example.com")
    admin = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin",
    )
    grant(actor=admin, role=PLATFORM_ADMIN_ROLE)
    console_schema = _schema("console")
    create_vendor = """
        mutation CreateVendor {
          createVendor(data: {
            slug: "console",
            displayName: "Console"
          }) {
            id
            slug
          }
        }
    """

    denied = _execute(console_schema, create_vendor, user=user)
    assert denied.errors is not None

    created = _data(_execute(console_schema, create_vendor, user=admin))
    vendor_id = created["createVendor"]["id"]
    assert created["createVendor"]["slug"] == "console"

    oauth_client = _data(
        _execute(
            console_schema,
            """
            mutation CreateOAuthClient($vendor: ID!) {
              createOauthClient(data: {
                vendor: $vendor,
                displayName: "Console prod",
                clientId: "console-client",
                isOidc: true,
                isEnabled: true,
                authorizeEndpoint: "https://issuer.example/authorize",
                tokenEndpoint: "https://issuer.example/token"
              }) {
                id
                displayName
                isOidc
              }
            }
            """,
            {"vendor": vendor_id},
            user=admin,
        )
    )["createOauthClient"]
    assert oauth_client["displayName"] == "Console prod"
    assert oauth_client["isOidc"] is True


def test_oauth_client_secret_never_appears_in_graphql_projection(
    iam_connection_tables: None,
) -> None:
    """Encrypted columns are absent from every IAM GraphQL schema."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    for sdl in (public_sdl, console_sdl):
        assert "clientSecret" not in sdl
        assert "material" not in sdl
        assert "identityClaims" not in sdl


def test_my_connected_accounts_are_scoped_to_session_user(
    iam_connection_tables: None,
) -> None:
    """A user's connected-account page excludes another user's credentials."""

    alice = User.objects.create_user(username="alice", email="alice@example.com")
    bob = User.objects.create_user(username="bob", email="bob@example.com")
    vendor, oauth_client = _vendor_and_oauth_client("scope", is_oidc=True, is_enabled=True)
    alice_account = ExternalAccount.objects.link(
        vendor,
        "alice-ext",
        owner=alice,
        email="alice@vendor.example",
    )
    bob_account = ExternalAccount.objects.link(
        vendor,
        "bob-ext",
        owner=bob,
        email="bob@vendor.example",
    )
    Credential.objects.upsert_for_user(
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

    data = _data(
        _execute(
            _schema("public"),
            """
            query {
              myConnectedAccounts(pagination: {limit: 10}) {
                results {
                  status
                  externalAccount { externalId email }
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


def test_unlink_account_only_removes_callers_credential(
    iam_connection_tables: None,
) -> None:
    """Unlinking deletes the session user's credential and leaves others alone."""

    alice = User.objects.create_user(username="unlink-alice", email="alice@example.com")
    bob = User.objects.create_user(username="unlink-bob", email="bob@example.com")
    vendor, oauth_client = _vendor_and_oauth_client("unlink", is_oidc=True, is_enabled=True)
    account = ExternalAccount.objects.link(
        vendor,
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
              unlinkAccount(externalAccountSqid: $sqid)
            }
            """,
            {"sqid": account.sqid},
            user=alice,
        )
    )

    assert data["unlinkAccount"] is True
    with system_context(reason="test assertions"):
        assert not Credential.objects.filter(user=alice, external_account=account).exists()
        assert Credential.objects.filter(user=bob, external_account=account).exists()


@pytest.fixture()
def iam_connection_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete connection tables for IAM GraphQL tests."""

    del transactional_db
    created_models = _create_connection_tables()
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _schema(name: str) -> Any:
    """Build one IAM-only GraphQL schema bucket."""

    entry = iam_schema.schemas[name]
    parts = {
        key: tuple(entry.get(key, ()))
        for key in SCHEMA_PART_KEYS
    }
    return GraphQLSchemas.from_addons([_Addon({name: parts})]).build(name)


def _execute(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a request-shaped context."""

    request = request or _request(user or AnonymousUser())
    actor = getattr(request, "user", AnonymousUser())
    with actor_context(actor):
        return schema.execute_sync(
            query,
            variable_values=variables or {},
            context_value=SimpleNamespace(request=request),
        )


def _data(result: Any) -> dict[str, Any]:
    """Return result data after asserting the operation succeeded."""

    assert result.errors is None
    assert result.data is not None
    return cast(dict[str, Any], result.data)


def _request(user: Any) -> Any:
    """Return a request object with a minimal mutable session."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    request.session = _Session()
    return request


def _vendor_and_oauth_client(
    slug: str,
    **oauth_client_overrides: Any,
) -> tuple[Vendor, OAuthClient]:
    """Create one vendor and OAuth client under system context."""

    defaults: dict[str, Any] = {
        "display_name": f"{slug.title()} prod",
        "client_id": f"{slug}-client",
        "client_secret": "secret",
        "issuer": "https://issuer.example",
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
        vendor = Vendor.objects.create(
            slug=slug,
            display_name=slug.title(),
            icon=f"{slug}.svg",
        )
        oauth_client = OAuthClient.objects.create(vendor=vendor, **defaults)
    return vendor, oauth_client


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


class _Addon:
    """Small addon stand-in exposing normalized schema parts."""

    def __init__(self, schema_parts: dict[str, dict[str, tuple[object, ...]]]) -> None:
        self.schema_parts = schema_parts
