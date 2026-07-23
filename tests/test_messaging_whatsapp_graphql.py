"""Tests for WhatsApp-specific channel GraphQL behavior."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.constants import RUN_SESSION_TASK
from angee.messaging_integrate_whatsapp.constants import SESSION_QUEUE
from tests.conftest import SchemaAddon, Vendor, execute_schema, make_integration
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
        "angee.integrate.impl.enqueue_task",
        lambda name, *, kwargs, queue=None, expires=None, **_: sent.append(
            {"name": name, "kwargs": kwargs, "queue": queue, "expires": expires}
        ),
    )
    return sent


def test_connect_whatsapp_channel_starts_pairing(whatsapp_graphql: list[dict[str, Any]]) -> None:
    """Connect declares the operator's intent up front, then starts pairing.

    ``lifecycle`` is declared connection intent, not proof of a linked device:
    asking to connect a channel connects it, and how far pairing has actually
    got is runtime state the session reports through ``sync_progress``. Keeping
    the row disconnected until the worker proved a JID overloaded
    ``disconnected`` with "pairing in progress", which is exactly why an
    operator's own disconnect could not stop a live session.
    """

    admin = _platform_admin("msg-wa-connect-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Personal WhatsApp"})["connect_whatsapp_channel"]

    assert payload["display_name"] == "Personal WhatsApp"
    assert payload["backend_class"] == "WHATSAPP"
    assert payload["lifecycle"] == "CONNECTED"
    with system_context(reason="test.messaging.whatsapp.connect.verify"):
        saved = Channel.objects.get(sqid=payload["id"])
        assert saved.owner_id == admin.pk
        assert saved.vendor.slug == "whatsapp"
        assert saved.credential_id is None
        assert saved.subscription_state["desired"] == Channel.LiveState.LIVE
    assert whatsapp_graphql == [
        {
            "name": RUN_SESSION_TASK,
            "kwargs": {"model_label": saved._meta.label_lower, "pk": saved.pk},
            "queue": SESSION_QUEUE,
            "expires": 60.0,
        }
    ]


def test_connect_whatsapp_channel_requires_seeded_vendor(messaging_graphql_tables: None) -> None:
    """The mutation reads the addon-owned vendor catalogue row; it never creates it."""

    admin = _platform_admin("msg-wa-missing-vendor-admin")
    result = execute_schema(_schema(), _CONNECT_MUTATION, {"name": "Personal"}, request=_request(admin))

    assert result.errors
    message = str(result.errors[0])
    assert "whatsapp" in message and "resources load" in message
    with system_context(reason="test.messaging.whatsapp.vendor.verify"):
        assert not Vendor.objects.filter(slug="whatsapp").exists()


def test_pairing_query_merges_durable_identity_with_lifecycle(
    whatsapp_graphql: list[dict[str, Any]],
) -> None:
    """A reopened dialog reconstructs paired/paused state without transient QR data."""

    del whatsapp_graphql
    admin = _platform_admin("msg-wa-pairing-query-admin")
    with system_context(reason="test.messaging.whatsapp.pairing_query.seed"):
        channel = make_integration(
            "msg-wa-pairing-query",
            model=Channel,
            backend_class="whatsapp",
            lifecycle="disconnected",
        )
        channel.merge_subscription_state(own_jid="4917000001@s.whatsapp.net")
        channel.connect()

    paired = _mutate(admin, _PAIRING_QUERY, {"id": channel.sqid})["channel_pairing"]
    assert paired == {
        # A real GraphQL enum now: the wire value is the upper-case member name.
        "state": "PAIRED",
        "qr": "",
        "own_id": "4917000001@s.whatsapp.net",
        "account_label": "+4917000001",
        "duplicate_channel_id": "",
        "duplicate_channel_name": "",
    }

    with system_context(reason="test.messaging.whatsapp.pairing_query.pause"):
        channel.pause()
    paused = _mutate(admin, _PAIRING_QUERY, {"id": channel.sqid})["channel_pairing"]
    assert paused["state"] == "PAUSED"


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

_PAIRING_QUERY = """
query ChannelPairing($id: ID!) {
  channel_pairing(id: $id) {
    state
    qr
    own_id
    account_label
    duplicate_channel_id
    duplicate_channel_name
  }
}
"""
