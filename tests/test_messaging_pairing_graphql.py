"""Tests for messaging-owned live-channel pairing GraphQL verbs."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate import live as live_module
from angee.integrate import tasks as tasks_module
from angee.integrate.constants import RUN_SESSION_TASK
from angee.integrate.live import session_store_path
from angee.integrate.models import IntegrationRuntimeStatus
from tests.conftest import SchemaAddon, execute_schema, make_integration
from tests.conftest import result_data as _data
from tests.pairing_backend import FakePairingBackend
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
def pairing_graphql(
    messaging_graphql_tables: None,
    settings: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Register a fake live backend and isolate its session store."""

    del messaging_graphql_tables
    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    settings.ANGEE_CHANNEL_BACKEND_CLASSES = {
        **settings.ANGEE_CHANNEL_BACKEND_CLASSES,
        FakePairingBackend.key: "tests.pairing_backend.FakePairingBackend",
    }
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.integrate.impl.enqueue_task",
        lambda name, *, kwargs, queue=None, expires=None, **_: sent.append(
            {"name": name, "kwargs": kwargs, "queue": queue, "expires": expires}
        ),
    )
    monkeypatch.setattr("angee.integrate.tasks.bridge_models", lambda _base: (Channel,))
    return sent


def test_channel_pairing_projects_neutral_identity(pairing_graphql: list[dict[str, Any]]) -> None:
    """The messaging query exposes generic identity and label fields."""

    del pairing_graphql
    admin = _platform_admin("msg-pairing-query-admin")
    channel = make_integration(
        "msg-pairing-query",
        model=Channel,
        backend_class=FakePairingBackend.key,
        lifecycle="connected",
    )
    with system_context(reason="test.messaging.pairing.query.seed"):
        channel.merge_subscription_state(own_id="account-1")

    payload = _data(
        execute_schema(
            _schema(),
            _PAIRING_QUERY,
            {"id": channel.sqid},
            request=_request(admin),
        )
    )["channel_pairing"]

    assert payload == {
        "state": "PAIRED",
        "qr": "",
        "own_id": "account-1",
        "account_label": "Account account-1",
        "duplicate_channel_id": "",
        "duplicate_channel_name": "",
    }


def test_pairing_projection_owns_the_pairing_wire_name(
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """The integrate-owned projection is registered under its own wire name."""

    del pairing_graphql
    rendered = _schema().as_str()

    assert "type Pairing {" in rendered
    assert "ChannelPairingType" not in rendered


@pytest.mark.parametrize(
    ("reported", "lifecycle", "identity", "expected", "expected_qr"),
    [
        (None, "connected", "", "STARTING", ""),
        ("awaiting_scan", "connected", "", "AWAITING_SCAN", "data:image/png;base64,qr"),
        (None, "connected", "account-1", "PAIRED", ""),
        (None, "paused", "account-1", "PAUSED", ""),
        ("logged_out", "connected", "account-1", "LOGGED_OUT", ""),
        (None, "disconnected", "account-1", "STOPPED", ""),
        ("duplicate_account", "connected", "account-2", "DUPLICATE_ACCOUNT", ""),
    ],
)
def test_channel_pairing_renders_every_lifecycle_and_report_state(
    pairing_graphql: list[dict[str, Any]],
    reported: str | None,
    lifecycle: str,
    identity: str,
    expected: str,
    expected_qr: str,
) -> None:
    """Lifecycle, claimed identity, report, and live desire keep their precedence."""

    del pairing_graphql
    admin = _platform_admin(f"msg-pairing-state-{expected.lower()}")
    channel = make_integration(
        f"msg-pairing-state-{expected.lower()}",
        model=Channel,
        backend_class=FakePairingBackend.key,
        lifecycle=lifecycle,
    )
    with system_context(reason="test.messaging.pairing.state.seed"):
        values = {"desired": Channel.LiveState.LIVE}
        if identity:
            values["own_id"] = identity
        channel.merge_subscription_state(**values)
        if reported:
            channel.sync_progress = {
                "details": {
                    "pairing": {
                        "state": reported,
                        "own_id": identity,
                        "qr": "data:image/png;base64,qr",
                    }
                }
            }
            channel.save(update_fields=["sync_progress", "updated_at"])

    payload = _execute(admin, _PAIRING_QUERY, {"id": channel.sqid})["channel_pairing"]

    assert payload["state"] == expected
    assert payload["qr"] == expected_qr


def test_reset_channel_pairing_wipes_store_and_restarts(
    pairing_graphql: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reset stops, proves exit, clears identity/store, and resumes the channel."""

    monkeypatch.setattr(live_module, "task_locks_are_cross_process", lambda: True)
    admin = _platform_admin("msg-pairing-reset-admin")
    channel = _live_channel("msg-pairing-reset")
    with system_context(reason="test.messaging.pairing.reset.seed"):
        channel.merge_subscription_state(own_id="account-1")
        channel.sync_progress = {"details": {"pairing": {"state": "paired", "own_id": "account-1"}}}
        channel.save(update_fields=["sync_progress", "updated_at"])
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"stale-session")

    payload = _execute(admin, _RESET_MUTATION, {"id": channel.sqid})

    assert payload["reset_channel_pairing"]["ok"] is True
    assert not store.exists()
    with system_context(reason="test.messaging.pairing.reset.verify"):
        channel.refresh_from_db()
        assert channel.lifecycle == "connected"
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
        assert "own_id" not in channel.subscription_state
        assert "pairing" not in channel.sync_progress.get("details", {})
    assert [entry["name"] for entry in pairing_graphql] == [RUN_SESSION_TASK]


def test_reset_channel_pairing_refuses_without_a_cross_process_lock(
    pairing_graphql: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Destructive reset never trusts a process-local view of session liveness."""

    del pairing_graphql
    monkeypatch.setattr(live_module, "task_locks_are_cross_process", lambda: False)
    admin = _platform_admin("msg-pairing-reset-blind-admin")
    channel = _live_channel("msg-pairing-reset-blind")
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"unproven-session")

    result = execute_schema(
        _schema(),
        _RESET_MUTATION,
        {"id": channel.sqid},
        request=_request(admin),
    )

    assert result.errors is not None
    assert "cross-process task lock backend" in str(result.errors[0])
    assert (store / "session.db").read_bytes() == b"unproven-session"


def test_disconnect_channel_stops_and_releases_ownership_but_retains_pairing(
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """Disconnect retains reusable identity and store while releasing intent."""

    del pairing_graphql
    admin = _platform_admin("msg-pairing-disconnect-admin")
    channel = _live_channel("msg-pairing-disconnect")
    with system_context(reason="test.messaging.pairing.disconnect.seed"):
        channel.merge_subscription_state(own_id="account-1")
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"linked-account")

    payload = _execute(admin, _DISCONNECT_MUTATION, {"id": channel.sqid})

    assert payload["disconnect_channel"]["ok"] is True
    assert store.exists()
    with system_context(reason="test.messaging.pairing.disconnect.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
        assert channel.subscription_state["own_id"] == "account-1"
        assert channel.lifecycle == "disconnected"


def test_resume_channel_pairing_is_idempotent_preserves_store_and_clears_error(
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """Resume re-arms a failed channel without destructively replacing its store."""

    admin = _platform_admin("msg-pairing-resume-admin")
    channel = _live_channel("msg-pairing-resume")
    with system_context(reason="test.messaging.pairing.resume.seed"):
        channel.runtime_status = IntegrationRuntimeStatus.ERROR
        channel.save(update_fields=["runtime_status", "updated_at"])
    store = session_store_path(channel) / "session.db"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"retained-session")

    first = _execute(admin, _RESUME_MUTATION, {"id": channel.sqid})
    second = _execute(admin, _RESUME_MUTATION, {"id": channel.sqid})

    assert first["resume_channel_pairing"]["ok"] is True
    assert second["resume_channel_pairing"]["ok"] is True
    assert store.read_bytes() == b"retained-session"
    channel.refresh_from_db()
    assert channel.runtime_status == IntegrationRuntimeStatus.OK
    assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 1}
    assert [entry["name"] for entry in pairing_graphql] == [
        RUN_SESSION_TASK,
        RUN_SESSION_TASK,
        RUN_SESSION_TASK,
    ]


def test_pairing_verbs_reject_poll_only_channels(pairing_graphql: list[dict[str, Any]]) -> None:
    """The generic surface is still restricted to live-capable channel backends."""

    del pairing_graphql
    admin = _platform_admin("msg-pairing-poll-only-admin")
    channel = make_integration(
        "msg-pairing-poll-only",
        model=Channel,
        backend_class="manual",
    )

    for operation in (
        _PAIRING_QUERY,
        _RESUME_MUTATION,
        _RESET_MUTATION,
        _DISCONNECT_MUTATION,
    ):
        result = execute_schema(
            _schema(),
            operation,
            {"id": channel.sqid},
            request=_request(admin),
        )
        assert result.errors
        assert "live channel" in str(result.errors[0])


@pytest.mark.parametrize(
    "operation_name",
    [
        "channel_pairing",
        "reset_channel_pairing",
        "resume_channel_pairing",
        "disconnect_channel",
    ],
)
def test_pairing_verbs_deny_non_admin_before_elevated_lookup(
    pairing_graphql: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    """Every pairing verb denies a reader before resolving the target elevated."""

    del pairing_graphql
    reader = get_user_model().objects.create_user(
        username=f"msg-pairing-reader-{operation_name}",
        email="pairing-reader@example.com",
    )
    channel = _live_channel(f"msg-pairing-reader-{operation_name}")
    operation = {
        "channel_pairing": _PAIRING_QUERY,
        "reset_channel_pairing": _RESET_MUTATION,
        "resume_channel_pairing": _RESUME_MUTATION,
        "disconnect_channel": _DISCONNECT_MUTATION,
    }[operation_name]
    lookups: list[str] = []

    def fail_lookup(*_args: Any, **_kwargs: Any) -> Any:
        lookups.append("elevated")
        raise AssertionError("permission gate must run before elevated lookup")

    monkeypatch.setattr(messaging_schema, "resolve_action_target", fail_lookup)
    monkeypatch.setattr(messaging_schema, "action_target", fail_lookup)

    result = execute_schema(
        _schema(),
        operation,
        {"id": channel.sqid},
        request=_request(reader),
    )

    assert result.errors is not None
    assert result.errors[0].message == "Platform admin permission required."
    assert result.errors[0].extensions == {"code": "PERMISSION_DENIED"}
    assert lookups == []


def test_channel_pairing_names_a_duplicate_from_the_callers_scope(
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """The report stays private; the query resolves the visible conflicting row."""

    del pairing_graphql
    admin = _platform_admin("msg-pairing-duplicate-admin")
    own_id = "account-1"
    owner = _live_channel("msg-pairing-duplicate-owner", display_name="Existing account")
    rejected = _live_channel("msg-pairing-duplicate-rejected")
    with system_context(reason="test.messaging.pairing.duplicate.seed"):
        owner.merge_subscription_state(own_id=own_id)
        rejected.runtime_status = IntegrationRuntimeStatus.ERROR
        rejected.sync_progress = {"details": {"pairing": {"state": "duplicate_account", "own_id": own_id}}}
        rejected.save(update_fields=["runtime_status", "sync_progress", "updated_at"])

    payload = _execute(admin, _PAIRING_QUERY, {"id": rejected.sqid})["channel_pairing"]

    assert payload["state"] == "DUPLICATE_ACCOUNT"
    assert payload["duplicate_channel_id"] == owner.sqid
    assert payload["duplicate_channel_name"] == "Existing account"
    assert owner.sqid not in str(rejected.sync_progress)
    assert owner.display_name not in str(rejected.sync_progress)


def _schema() -> Any:
    """Build the console schema without a vendor pairing contribution."""

    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def _execute(admin: Any, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute one pairing operation and return its successful payload."""

    return _data(execute_schema(_schema(), operation, variables, request=_request(admin)))


def _live_channel(slug: str, *, display_name: str = "") -> Any:
    """Create a connected channel backed by the vendor-free live fake."""

    return make_integration(
        slug,
        model=Channel,
        backend_class=FakePairingBackend.key,
        lifecycle="connected",
        display_name=display_name or slug,
    )


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

_RESET_MUTATION = """
mutation ResetChannelPairing($id: ID!) {
  reset_channel_pairing(id: $id) {
    ok
    message
  }
}
"""

_RESUME_MUTATION = """
mutation ResumeChannelPairing($id: ID!) {
  resume_channel_pairing(id: $id) {
    ok
    message
  }
}
"""

_DISCONNECT_MUTATION = """
mutation DisconnectChannel($id: ID!) {
  disconnect_channel(id: $id) {
    ok
    message
  }
}
"""
