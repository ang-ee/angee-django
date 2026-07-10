"""Tests for the IMAP-owned console connect flow."""

from __future__ import annotations

import importlib
from typing import Any

from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
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
        "lifecycle": "ACTIVE",
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
        assert saved.lifecycle == "active"
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
    assert "IMAP vendor" in str(result.errors[0])
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
    runtime_status
    config
  }
}
"""
