"""Tests for the WhatsApp-owned console connect/disconnect/reset flow."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging_integrate_whatsapp.backend import RUN_SESSION_TASK, SESSION_QUEUE
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


@pytest.fixture
def whatsapp_graphql(
    messaging_graphql_tables: None, settings: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, Any]]:
    """Vendor row + tmp data dir + a captured enqueue; returns the sent-task log."""

    del messaging_graphql_tables
    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    with system_context(reason="test.messaging.whatsapp.vendor.seed"):
        Vendor.objects.create(slug="whatsapp", display_name="WhatsApp")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.messaging_integrate_whatsapp.backend.enqueue_task",
        lambda name, *, kwargs, queue=None, expires=None, **_: sent.append(
            {"name": name, "kwargs": kwargs, "queue": queue, "expires": expires}
        ),
    )
    return sent


def test_connect_whatsapp_channel_starts_pairing(whatsapp_graphql: list[dict[str, Any]]) -> None:
    """Connect creates an active credential-less channel and dispatches the session."""

    admin = _platform_admin("msg-wa-connect-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Personal WhatsApp"})["connect_whatsapp_channel"]

    assert payload["display_name"] == "Personal WhatsApp"
    assert payload["backend_class"] == "WHATSAPP"
    assert payload["lifecycle"] == "ACTIVE"
    with system_context(reason="test.messaging.whatsapp.connect.verify"):
        saved = Channel.objects.get(sqid=payload["id"])
        assert saved.owner_id == admin.pk
        assert saved.vendor.slug == "whatsapp"
        assert saved.credential_id is None
        assert saved.subscription_state["desired"] == Channel.LiveState.LIVE
    assert whatsapp_graphql == [
        {
            "name": RUN_SESSION_TASK,
            "kwargs": {"channel_id": saved.pk},
            "queue": SESSION_QUEUE,
            "expires": 60.0,
        }
    ]


def test_connect_whatsapp_channel_requires_seeded_vendor(messaging_graphql_tables: None) -> None:
    """The mutation reads the addon-owned vendor catalogue row; it never creates it."""

    admin = _platform_admin("msg-wa-missing-vendor-admin")
    result = execute_schema(_schema(), _CONNECT_MUTATION, {"name": "Personal"}, request=_request(admin))

    assert result.errors
    assert "WhatsApp vendor" in str(result.errors[0])
    with system_context(reason="test.messaging.whatsapp.vendor.verify"):
        assert not Vendor.objects.filter(slug="whatsapp").exists()


def test_disconnect_stops_wipes_and_disables(whatsapp_graphql: list[dict[str, Any]]) -> None:
    """Disconnect waits for the session lock, removes the store, disables the row."""

    from angee.messaging_integrate_whatsapp.client import session_store_path

    admin = _platform_admin("msg-wa-disconnect-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "To unlink"})["connect_whatsapp_channel"]
    with system_context(reason="test.messaging.whatsapp.disconnect.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"linked-device")

    result = _mutate(admin, _DISCONNECT_MUTATION, {"id": payload["id"]})
    assert result["disconnect_whatsapp_channel"]["ok"] is True
    assert not store.exists()
    with system_context(reason="test.messaging.whatsapp.disconnect.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
        assert channel.lifecycle == "disabled"


def test_disconnect_refuses_while_session_holds_the_lock(
    whatsapp_graphql: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store an active session still has open is never unlinked underneath it."""

    from angee.messaging_integrate_whatsapp.client import session_store_path

    admin = _platform_admin("msg-wa-busy-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Busy"})["connect_whatsapp_channel"]
    with system_context(reason="test.messaging.whatsapp.busy.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "angee.messaging_integrate_whatsapp.connect.bridge_is_locked", lambda _bridge: True
    )
    monkeypatch.setattr("angee.messaging_integrate_whatsapp.connect.SESSION_EXIT_TIMEOUT", 0.1)
    result = execute_schema(
        _schema(), _DISCONNECT_MUTATION, {"id": payload["id"]}, request=_request(admin)
    )
    assert result.errors
    assert "still running" in str(result.errors[0])
    assert store.exists()


def test_reset_pairing_wipes_store_and_restarts(whatsapp_graphql: list[dict[str, Any]]) -> None:
    """Reset stops the session, wipes the device store, and re-dispatches pairing."""

    from angee.messaging_integrate_whatsapp.client import session_store_path

    admin = _platform_admin("msg-wa-reset-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Re-pair"})["connect_whatsapp_channel"]
    with system_context(reason="test.messaging.whatsapp.reset.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"stale-device")
    whatsapp_graphql.clear()

    result = _mutate(admin, _RESET_MUTATION, {"id": payload["id"]})
    assert result["reset_whatsapp_pairing"]["ok"] is True
    assert not store.exists()
    with system_context(reason="test.messaging.whatsapp.reset.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
    assert [entry["name"] for entry in whatsapp_graphql] == [RUN_SESSION_TASK]


def _schema() -> Any:
    """Build the console schema with the optional WhatsApp addon installed."""

    whatsapp_schema = importlib.import_module("angee.messaging_integrate_whatsapp.schema")
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema, whatsapp_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def _mutate(admin: Any, mutation: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute one addon mutation and return its data payload."""

    result = execute_schema(_schema(), mutation, variables, request=_request(admin))
    return _data(result)


_CONNECT_MUTATION = """
mutation ConnectWhatsapp($name: String!) {
  connect_whatsapp_channel(name: $name) {
    id
    display_name
    backend_class
    lifecycle
  }
}
"""

_DISCONNECT_MUTATION = """
mutation DisconnectWhatsapp($id: ID!) {
  disconnect_whatsapp_channel(id: $id) {
    ok
    message
  }
}
"""

_RESET_MUTATION = """
mutation ResetWhatsappPairing($id: ID!) {
  reset_whatsapp_pairing(id: $id) {
    ok
    message
  }
}
"""
