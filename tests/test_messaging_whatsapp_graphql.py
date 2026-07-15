"""Tests for the WhatsApp-owned console connect/disconnect/reset flow."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.models import IntegrationRuntimeStatus
from angee.messaging_integrate_whatsapp.backend import RUN_SESSION_TASK, SESSION_QUEUE
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
        "angee.messaging_integrate_whatsapp.backend.enqueue_task",
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


def test_disconnect_stops_and_releases_ownership_but_retains_pairing(
    whatsapp_graphql: list[dict[str, Any]],
) -> None:
    """Disconnect retains a reusable store/JID while moving to disconnected."""

    from angee.messaging_integrate_whatsapp.client import session_store_path

    admin = _platform_admin("msg-wa-disconnect-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "To unlink"})["connect_whatsapp_channel"]
    with system_context(reason="test.messaging.whatsapp.disconnect.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
        channel.merge_subscription_state(own_jid="4917000001@s.whatsapp.net")
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"linked-device")

    result = _mutate(admin, _DISCONNECT_MUTATION, {"id": payload["id"]})
    assert result["disconnect_whatsapp_channel"]["ok"] is True
    assert store.exists()
    with system_context(reason="test.messaging.whatsapp.disconnect.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
        assert channel.subscription_state["own_jid"] == "4917000001@s.whatsapp.net"
        assert channel.lifecycle == "disconnected"


def test_reset_pairing_wipes_store_and_restarts(
    whatsapp_graphql: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reset stops the session, wipes the device store, and re-dispatches pairing."""

    from angee.messaging_integrate_whatsapp import connect as connect_module
    from angee.messaging_integrate_whatsapp.client import session_store_path

    # Reset is destructive and proves the session released its store through the
    # bridge lock, so it requires a lock backend that can see another process's
    # session at all. Tests run on the SQLite/process-local floor.
    monkeypatch.setattr(connect_module, "task_locks_are_cross_process", lambda: True)
    admin = _platform_admin("msg-wa-reset-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Re-pair"})["connect_whatsapp_channel"]
    with system_context(reason="test.messaging.whatsapp.reset.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
        channel.merge_subscription_state(own_jid="4917000001@s.whatsapp.net")
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"stale-device")
    whatsapp_graphql.clear()

    result = _mutate(admin, _RESET_MUTATION, {"id": payload["id"]})
    assert result["reset_whatsapp_pairing"]["ok"] is True
    assert not store.exists()
    with system_context(reason="test.messaging.whatsapp.reset.verify"):
        channel.refresh_from_db()
        # Reset lands on the resumed shape: identity gone, store gone, but the
        # operator still wants this channel connected — so a session can run.
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
        assert "own_jid" not in channel.subscription_state
        assert channel.lifecycle == "connected"
    assert [entry["name"] for entry in whatsapp_graphql] == [RUN_SESSION_TASK]


def test_reset_pairing_refuses_on_a_process_local_lock_backend(
    whatsapp_graphql: list[dict[str, Any]],
) -> None:
    """Reset declines rather than wipe a store it cannot prove was released.

    ``bridge_is_locked`` answers off the task lock backend, and a process-local
    backend cannot see a lock a worker holds in another process — it returns
    ``False`` for a session that is running right now. That is the SQLite/dev
    floor, where ``angee dev`` runs web and celery as separate processes, so the
    bounded wait would return at once and hand a live store to ``rmtree``.
    """

    from angee.messaging_integrate_whatsapp import connect as connect_module
    from angee.messaging_integrate_whatsapp.client import session_store_path

    assert connect_module.task_locks_are_cross_process() is False
    admin = _platform_admin("msg-wa-reset-blind-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Blind reset"})["connect_whatsapp_channel"]
    with system_context(reason="test.messaging.whatsapp.reset.blind.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"a store no probe here can vouch for")

    result = execute_schema(_schema(), _RESET_MUTATION, {"id": payload["id"]}, request=_request(admin))

    assert result.errors is not None
    assert "cross-process task lock backend" in str(result.errors[0])
    assert (store / "session.db").read_bytes() == b"a store no probe here can vouch for"


def test_resume_pairing_is_idempotent_and_preserves_the_device_store(
    whatsapp_graphql: list[dict[str, Any]],
) -> None:
    """An existing channel can resume without destructive re-pairing."""

    from angee.messaging_integrate_whatsapp.client import session_store_path

    admin = _platform_admin("msg-wa-resume-admin")
    payload = _mutate(admin, _CONNECT_MUTATION, {"name": "Reconnect"})[
        "connect_whatsapp_channel"
    ]
    with system_context(reason="test.messaging.whatsapp.resume.seed"):
        channel = Channel.objects.get(sqid=payload["id"])
    store = session_store_path(channel) / "session.db"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"linked-device")
    whatsapp_graphql.clear()

    first = _mutate(admin, _RESUME_MUTATION, {"id": payload["id"]})
    second = _mutate(admin, _RESUME_MUTATION, {"id": payload["id"]})

    assert first["resume_whatsapp_pairing"]["ok"] is True
    assert second["resume_whatsapp_pairing"]["ok"] is True
    assert store.read_bytes() == b"linked-device"
    assert [entry["name"] for entry in whatsapp_graphql] == [
        RUN_SESSION_TASK,
        RUN_SESSION_TASK,
    ]


def test_resume_pairing_rejects_non_whatsapp_channels(
    whatsapp_graphql: list[dict[str, Any]],
) -> None:
    """The addon action cannot start an arbitrary messaging backend."""

    del whatsapp_graphql
    admin = _platform_admin("msg-wa-resume-kind-admin")
    channel = make_integration("msg-wa-resume-kind", model=Channel, backend_class="manual")

    result = execute_schema(
        _schema(),
        _RESUME_MUTATION,
        {"id": channel.sqid},
        request=_request(admin),
    )

    assert result.errors
    assert "WhatsApp" in str(result.errors[0])


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

    paired = _mutate(admin, _PAIRING_QUERY, {"id": channel.sqid})["whatsapp_pairing"]
    assert paired == {
        # A real GraphQL enum now: the wire value is the upper-case member name.
        "state": "PAIRED",
        "qr": "",
        "jid": "4917000001@s.whatsapp.net",
        "phone": "+4917000001",
        "duplicate_channel_id": "",
        "duplicate_channel_name": "",
    }

    with system_context(reason="test.messaging.whatsapp.pairing_query.pause"):
        channel.pause()
    paused = _mutate(admin, _PAIRING_QUERY, {"id": channel.sqid})["whatsapp_pairing"]
    assert paused["state"] == "PAUSED"


def test_pairing_query_names_a_duplicate_from_the_callers_own_scope(
    whatsapp_graphql: list[dict[str, Any]],
) -> None:
    """The conflicting channel is named by the admin-gated query, never by the report.

    The rejected channel's ``sync_progress`` is row-scoped and broadcast over
    ``channelChanged``, so it must never carry the foreign owner's sqid or name.
    Resolving the conflict here instead keeps the naming behind both the admin
    gate and the caller's own read scope.
    """

    del whatsapp_graphql
    admin = _platform_admin("msg-wa-duplicate-admin")
    jid = "4917000001@s.whatsapp.net"
    with system_context(reason="test.messaging.whatsapp.duplicate.seed"):
        owner = make_integration(
            "msg-wa-duplicate-owner",
            model=Channel,
            backend_class="whatsapp",
            lifecycle="disconnected",
            display_name="Alice's personal phone",
        )
        owner.merge_subscription_state(own_jid=jid)
        owner.connect()
        rejected = make_integration(
            "msg-wa-duplicate-rejected",
            model=Channel,
            backend_class="whatsapp",
        )
        # What the rejection actually leaves behind: the operator's CONNECTED
        # lifecycle stands, the failure is on runtime_status, and the report
        # carries the bare state plus the JID the caller themselves scanned —
        # nothing about the channel that owns it.
        rejected.runtime_status = IntegrationRuntimeStatus.ERROR
        rejected.sync_progress = {"details": {"pairing": {"state": "duplicate_account", "jid": jid}}}
        rejected.save(update_fields=["runtime_status", "sync_progress", "updated_at"])

    payload = _mutate(admin, _PAIRING_QUERY, {"id": rejected.sqid})["whatsapp_pairing"]

    assert payload["state"] == "DUPLICATE_ACCOUNT"
    assert payload["duplicate_channel_id"] == owner.sqid
    assert payload["duplicate_channel_name"] == "Alice's personal phone"


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

_RESUME_MUTATION = """
mutation ResumeWhatsappPairing($id: ID!) {
  resume_whatsapp_pairing(id: $id) {
    ok
    message
  }
}
"""

_PAIRING_QUERY = """
query WhatsappPairing($id: ID!) {
  whatsapp_pairing(id: $id) {
    state
    qr
    jid
    phone
    duplicate_channel_id
    duplicate_channel_name
  }
}
"""
