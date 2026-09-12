"""Tests for the IMAP-owned console connect flow."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from imapclient.exceptions import LoginError
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.messaging_integrate_imap.backend import ImapChannelBackend
from tests.conftest import SchemaAddon, Vendor, execute_schema
from tests.conftest import result_data as _data
from tests.test_messaging_graphql import (
    Channel,
    _platform_admin,
    _request,
    iam_schema,
    integrate_schema,
    messaging_schema,
    parties_schema,
)
from tests.test_messaging_imap import FakeImapAccount, FakeIMAPClient

pytest_plugins = ("tests.test_messaging_graphql",)


def test_connect_imap_channel_creates_basic_auth_channel(messaging_graphql_tables: None) -> None:
    """The IMAP addon creates the credential and channel together."""

    admin = _platform_admin("msg-imap-connect-admin")
    _seed_imap_vendor()

    channel = _connect(
        admin,
        {
            "name": "Ada Mail",
            "host": "imap.example.com",
            "security": "starttls",
            "port": 143,
            "username": "ada@example.com",
            "password": "mail-password",
            "mailboxes": ["INBOX", "Archive"],
            "ownAddresses": ["ada@example.com", "a.lovelace@example.com"],
        },
    )

    assert channel == {
        "id": channel["id"],
        "display_name": "Ada Mail",
        "backend_class": "IMAP",
        "lifecycle": "CONNECTED",
        "credential_status": "active",
        "runtime_status": "OK",
        "config": {
            "host": "imap.example.com",
            "security": "starttls",
            "port": 143,
            "mailboxes": ["INBOX", "Archive"],
            "own_addresses": ["ada@example.com", "a.lovelace@example.com"],
        },
    }
    with system_context(reason="test.messaging.imap.connect.verify"):
        saved = Channel.objects.get(sqid=channel["id"])
        assert saved.owner_id == admin.pk
        assert saved.created_by_id == admin.pk
        assert saved.vendor.slug == "imap"
        assert saved.backend_class == "imap"
        assert saved.lifecycle == "connected"
        assert saved.runtime_status == "ok"
        assert saved.credential.kind == CredentialKind.BASIC_AUTH
        assert saved.credential.reveal() == {
            "username": "ada@example.com",
            "password": "mail-password",
        }


def test_connect_imap_channel_reuses_no_credentials_by_label(messaging_graphql_tables: None) -> None:
    """Two channels with the same display name keep separate Basic-auth secrets."""

    admin = _platform_admin("msg-imap-repeat-admin")
    _seed_imap_vendor()

    first = _connect(
        admin,
        {
            "name": "Shared Mail",
            "host": "imap.one.example.com",
            "security": "ssl",
            "port": None,
            "username": "first@example.com",
            "password": "first-password",
            "mailboxes": None,
            "ownAddresses": None,
        },
    )
    second = _connect(
        admin,
        {
            "name": "Shared Mail",
            "host": "imap.two.example.com",
            "security": "ssl",
            "port": None,
            "username": "second@example.com",
            "password": "second-password",
            "mailboxes": None,
            "ownAddresses": None,
        },
    )

    with system_context(reason="test.messaging.imap.repeat.verify"):
        first_channel = Channel.objects.get(sqid=first["id"])
        second_channel = Channel.objects.get(sqid=second["id"])
        assert first_channel.credential_id != second_channel.credential_id
        assert first_channel.credential.reveal() == {
            "username": "first@example.com",
            "password": "first-password",
        }
        assert second_channel.credential.reveal() == {
            "username": "second@example.com",
            "password": "second-password",
        }


def test_connect_imap_channel_requires_seeded_vendor(messaging_graphql_tables: None) -> None:
    """The mutation reads the addon-owned vendor catalogue row; it never creates it."""

    admin = _platform_admin("msg-imap-missing-vendor-admin")

    result = execute_schema(
        _schema(),
        _CONNECT_MUTATION,
        {
            "name": "Ada Mail",
            "host": "imap.example.com",
            "security": "ssl",
            "port": None,
            "username": "ada@example.com",
            "password": "mail-password",
            "mailboxes": None,
            "ownAddresses": None,
        },
        request=_request(admin),
    )

    assert result.errors
    assert result.errors[0].message == "IMAP integration is not configured."
    assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    with system_context(reason="test.messaging.imap.vendor.verify"):
        assert not Vendor.objects.filter(slug="imap").exists()


def _schema() -> Any:
    """Build the console schema with the optional IMAP addon installed."""

    imap_schema = importlib.import_module("angee.messaging_integrate_imap.schema")
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema, imap_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def test_update_imap_channel_credential_replaces_the_login_in_place(
    messaging_graphql_tables: None,
) -> None:
    """The record verb rotates the Basic-auth material; channel and credential rows stay."""

    admin = _platform_admin("msg-imap-rotate-admin")
    _seed_imap_vendor()
    channel = _connect(admin, _CONNECT_VARIABLES)
    with system_context(reason="test.messaging.imap.rotate.before"):
        saved = Channel.objects.get(sqid=channel["id"])
        credential_pk = saved.credential.pk

    result = _data(
        execute_schema(
            _schema(),
            _UPDATE_CREDENTIAL_MUTATION,
            {"id": channel["id"], "username": "ada.lovelace@example.com", "password": "rotated"},
            request=_request(admin),
        )
    )["update_imap_channel_credential"]

    assert result == {"ok": True, "message": "Credential updated."}
    with system_context(reason="test.messaging.imap.rotate.verify"):
        saved = Channel.objects.get(sqid=channel["id"])
        assert saved.credential.pk == credential_pk
        assert saved.credential.reveal() == {"username": "ada.lovelace@example.com", "password": "rotated"}


def test_update_imap_channel_credential_refuses_blank_material(messaging_graphql_tables: None) -> None:
    """The kind handler's validation reaches the operator in band."""

    admin = _platform_admin("msg-imap-rotate-blank-admin")
    _seed_imap_vendor()
    channel = _connect(admin, _CONNECT_VARIABLES)

    result = _data(
        execute_schema(
            _schema(),
            _UPDATE_CREDENTIAL_MUTATION,
            {"id": channel["id"], "username": "ada@example.com", "password": ""},
            request=_request(admin),
        )
    )["update_imap_channel_credential"]

    assert result["ok"] is False
    assert "username and password" in result["message"]
    with system_context(reason="test.messaging.imap.rotate.blank.verify"):
        saved = Channel.objects.get(sqid=channel["id"])
        assert saved.credential.reveal()["password"] == "mail-password"


def test_test_connection_logs_in_through_the_imap_backend(
    messaging_graphql_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic integration verb reaches the channel backend's real login."""

    admin = _platform_admin("msg-imap-test-admin")
    _seed_imap_vendor()
    channel = _connect(admin, {**_CONNECT_VARIABLES, "host": "10.0.0.4"})
    account = FakeImapAccount({"INBOX": {"flags": (b"\\HasNoChildren",), "uidvalidity": 1, "messages": {}}})
    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)

    result = _data(
        execute_schema(_schema(), _TEST_CONNECTION_MUTATION, {"id": channel["id"]}, request=_request(admin))
    )["test_connection"]

    assert result == {"ok": True, "message": "Signed in to 10.0.0.4 as ada@example.com."}
    assert account.logins == [("login", "ada@example.com", "mail-password")]
    assert account.logouts == 1


def test_test_connection_reports_the_imap_refusal(
    messaging_graphql_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected login reaches the operator with the account and host named."""

    admin = _platform_admin("msg-imap-test-refused-admin")
    _seed_imap_vendor()
    channel = _connect(admin, {**_CONNECT_VARIABLES, "host": "10.0.0.4"})
    account = FakeImapAccount({})
    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)

    def refuse(self: FakeIMAPClient, username: str, password: str) -> None:
        del self, username, password
        raise LoginError("[AUTHENTICATIONFAILED] unexpected vendor payload mail-password")

    monkeypatch.setattr(FakeIMAPClient, "login", refuse)

    result = _data(
        execute_schema(_schema(), _TEST_CONNECTION_MUTATION, {"id": channel["id"]}, request=_request(admin))
    )["test_connection"]

    assert result == {
        "ok": False,
        "message": "IMAP login failed for 'ada@example.com' at 10.0.0.4. Check the account credentials.",
    }


def _seed_imap_vendor() -> None:
    """Seed the vendor row normally loaded from messaging_integrate_imap resources."""

    with system_context(reason="test.messaging.imap.vendor.seed"):
        Vendor.objects.create(slug="imap", display_name="IMAP")


def _connect(admin: Any, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute the addon-owned connect mutation and return its channel payload."""

    result = execute_schema(_schema(), _CONNECT_MUTATION, variables, request=_request(admin))
    return _data(result)["connect_imap_channel"]


_CONNECT_MUTATION = """
mutation ConnectImap(
  $name: String!
  $host: String!
  $security: String!
  $port: Int
  $username: String!
  $password: String!
  $mailboxes: [String!]
  $ownAddresses: [String!]
) {
  connect_imap_channel(
    name: $name
    host: $host
    security: $security
    port: $port
    username: $username
    password: $password
    mailboxes: $mailboxes
    own_addresses: $ownAddresses
  ) {
    id
    display_name
    backend_class
    lifecycle
    credential_status
    runtime_status
    config
  }
}
"""

_CONNECT_VARIABLES = {
    "name": "Ada Mail",
    "host": "imap.example.com",
    "security": "ssl",
    "username": "ada@example.com",
    "password": "mail-password",
}

_UPDATE_CREDENTIAL_MUTATION = """
mutation UpdateImapCredential($id: ID!, $username: String!, $password: String!) {
  update_imap_channel_credential(id: $id, username: $username, password: $password) {
    ok
    message
  }
}
"""

_TEST_CONNECTION_MUTATION = """
mutation TestConnection($id: ID!) {
  test_connection(id: $id) {
    ok
    message
  }
}
"""
