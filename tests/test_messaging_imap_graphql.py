"""Tests for the IMAP-owned console connect flow."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from imapclient.exceptions import LoginError
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.integrate.streams import CursorInvalid, advance_stream
from angee.messaging_integrate_imap.backend import ImapChannelBackend
from tests.conftest import SchemaAddon, Vendor, create_user, execute_schema
from tests.conftest import result_data as _data
from tests.integrate_models import SyncStream
from tests.test_messaging_graphql import (
    Channel,
    _platform_admin,
    _request,
    iam_schema,
    integrate_schema,
    messaging_schema,
    parties_schema,
)
from tests.test_messaging_imap import FakeImapAccount, FakeIMAPClient, _eml, _folder

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
    """The kind handler's validation follows the IMAP BAD_USER_INPUT contract."""

    admin = _platform_admin("msg-imap-rotate-blank-admin")
    _seed_imap_vendor()
    channel = _connect(admin, _CONNECT_VARIABLES)

    result = execute_schema(
        _schema(),
        _UPDATE_CREDENTIAL_MUTATION,
        {"id": channel["id"], "username": "ada@example.com", "password": ""},
        request=_request(admin),
    )

    assert result.errors
    assert "username and password" in result.errors[0].message
    assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    with system_context(reason="test.messaging.imap.rotate.blank.verify"):
        saved = Channel.objects.get(sqid=channel["id"])
        assert saved.credential.reveal()["password"] == "mail-password"


def test_sample_preview_is_a_paged_query_with_no_mutation_or_dead_output(
    messaging_graphql_tables: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, channel, account = _paused_sample_channel(monkeypatch)
    schema = _schema()
    first = _data(execute_schema(schema, _PREVIEW_QUERY, {"id": channel["id"]}, request=_request(admin)))[
        "preview_imap_sample"
    ]
    second = _data(execute_schema(schema, _PREVIEW_QUERY, {
        "id": channel["id"], "uidvalidity": first["uidvalidity"], "upperUid": first["upper_uid"],
        "beforeUid": first["next_before_uid"], "totalCount": first["total_count"],
    }, request=_request(admin)))["preview_imap_sample"]

    assert first["messages"] == [{"uid": 3, "subject": "3"}, {"uid": 2, "subject": "2"}]
    assert second["messages"] == [{"uid": 1, "subject": "1"}]
    assert first["total_count"] == second["total_count"] == 3
    assert second["next_before_uid"] is None
    assert "preview_imap_sample" not in schema._schema.mutation_type.fields
    assert "truncated" not in schema._schema.type_map["ImapSamplePreview"].fields
    assert account.logouts == 2
    with system_context(reason="test.messaging.imap.preview.cursor"):
        saved = Channel.objects.get(sqid=channel["id"])
        stream = SyncStream.objects.current(saved, "messages", "INBOX")
        assert stream.cursor == {"uidvalidity": 100, "last_uid": 1}
        assert saved.cursor == {}


def test_sample_preview_denies_non_admin_before_mailbox_probe(
    messaging_graphql_tables: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, channel, account = _paused_sample_channel(monkeypatch)
    reader = create_user("imap-sample-reader")

    result = execute_schema(_schema(), _PREVIEW_QUERY, {"id": channel["id"]}, request=_request(reader))

    assert result.errors
    assert account.logins == []


@pytest.mark.parametrize("operation", ["preview", "import", "prepare"])
def test_imap_resolvers_preserve_safe_transport_errors(
    messaging_graphql_tables: None, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    admin, channel, _ = _paused_sample_channel(monkeypatch)

    def refuse(self: FakeIMAPClient, username: str, password: str) -> None:
        raise LoginError("vendor payload mail-password")

    monkeypatch.setattr(FakeIMAPClient, "login", refuse)
    document = {
        "preview": _PREVIEW_QUERY,
        "import": """mutation($id: ID!) {
            import_imap_sample(id: $id, mailbox: "INBOX", uidvalidity: 100, uids: [1]) { imported_uids }
        }""",
        "prepare": """mutation($id: ID!) { prepare_imap_new_mail(id: $id) { ok message } }""",
    }[operation]

    result = execute_schema(_schema(), document, {"id": channel["id"]}, request=_request(admin))

    assert result.errors
    assert result.errors[0].message == (
        "IMAP login failed for 'ada@example.com' at 10.0.0.4. Check the account credentials."
    )
    assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}


def _paused_sample_channel(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any], FakeImapAccount]:
    """Create one admitted channel and substitute only the remote IMAP server."""

    admin = _platform_admin("msg-imap-sample-admin")
    _seed_imap_vendor()
    channel = _connect(admin, {**_CONNECT_VARIABLES, "host": "10.0.0.4", "mailboxes": ["INBOX"]})
    with system_context(reason="test.messaging.imap.sample.pause"):
        saved = Channel.objects.get(sqid=channel["id"])
        saved.pause()
        SyncStream.objects.current(saved, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": 1})
    account = FakeImapAccount({"INBOX": _folder(*(_eml(subject=str(uid)) for uid in range(1, 4)))})
    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)
    return admin, channel, account


def test_prepare_imap_new_mail_is_future_only_idempotent_and_epoch_safe(
    messaging_graphql_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paused action skips the current UID set once and never backfills after an epoch change."""

    admin = _platform_admin("msg-imap-new-mail-admin")
    _seed_imap_vendor()
    channel = _connect(admin, {**_CONNECT_VARIABLES, "host": "10.0.0.4", "mailboxes": ["INBOX"]})
    account = FakeImapAccount(
        {
            "INBOX": {
                "flags": (b"\\HasNoChildren",),
                "uidvalidity": 100,
                "messages": {
                    1: {"raw": _eml(subject="old one", message_id="<old-1@example.com>")},
                    2: {"raw": _eml(subject="old two", message_id="<old-2@example.com>")},
                },
            }
        }
    )
    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)
    with system_context(reason="test.messaging.imap.new_mail.pause"):
        saved = Channel.objects.get(sqid=channel["id"])
        SyncStream.objects.current(saved, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": 1})
        saved.pause()

    with system_context(reason="test.messaging.imap.new_mail.first"):
        saved = Channel.objects.get(sqid=channel["id"])
    assert saved.prepare_imap_new_mail(actor=admin) == (1, True)

    account.folders["INBOX"]["messages"][3] = {"raw": _eml(subject="new", message_id="<new@example.com>")}
    with system_context(reason="test.messaging.imap.new_mail.repeat"):
        saved = Channel.objects.get(sqid=channel["id"])
    assert saved.prepare_imap_new_mail(actor=admin) == (1, False)

    with system_context(reason="test.messaging.imap.new_mail.verify"):
        saved = Channel.objects.get(sqid=channel["id"])
        stream = SyncStream.objects.current(saved, "messages", "INBOX")
        assert saved.config["delivery_mode"] == "new_only"
        assert len(saved.config["source_identity"]) == 64
        assert stream.cursor["uidvalidity"] == 100
        assert stream.cursor["last_uid"] == 2
        backend = saved.backend
        try:
            page = backend.extract(stream, 200)
            assert [message.subject for message in page.records] == ["new"]
        finally:
            backend.close()

        stream.resync_required = True
        stream.save(update_fields=["resync_required"])
        backend = saved.backend
        try:
            reset = advance_stream(stream, backend)
            assert reset.reset
            resumed = advance_stream(reset.stream, backend)
            assert resumed.count == 0
            assert resumed.stream.cursor["last_uid"] == 3
        finally:
            backend.close()

        account.folders["INBOX"]["uidvalidity"] = 200
        backend = saved.backend
        try:
            with pytest.raises(CursorInvalid) as invalid:
                backend.extract(stream, 200)
            assert saved.config["delivery_mode"] == "new_only"
            assert invalid.value.cursor["uidvalidity"] == 200
            assert invalid.value.cursor["last_uid"] == 3
        finally:
            backend.close()


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

_PREVIEW_QUERY = """
query PreviewImapSample($id: ID!, $uidvalidity: Int, $upperUid: Int, $beforeUid: Int, $totalCount: Int) {
  preview_imap_sample(id: $id, mailbox: "INBOX", all_dates: true, limit: 2,
    uidvalidity: $uidvalidity, upper_uid: $upperUid, before_uid: $beforeUid, total_count: $totalCount) {
    mailbox uidvalidity upper_uid total_count next_before_uid
    messages { uid subject }
  }
}
"""

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
