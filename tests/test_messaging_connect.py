"""Tests for messaging-owned unfinished pairing selection and restart services."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rebac import system_context

from angee.integrate import live as live_module
from angee.integrate.constants import RUN_SESSION_TASK
from angee.integrate.live import PairingState, session_store_path
from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus
from angee.messaging import connect
from angee.messaging.testing.models import Channel, Message
from tests.conftest import Vendor, make_integration
from tests.pairing_backend import FakePairingBackend

pytest_plugins = ("tests.test_messaging_pairing_graphql",)


@pytest.fixture
def pairing_user(pairing_graphql: list[dict[str, Any]]) -> Any:
    """Seed the neutral backend's vendor and an ordinary channel owner."""

    del pairing_graphql
    with system_context(reason="test.messaging.connect.user"):
        Vendor.objects.create(slug=FakePairingBackend.key, display_name=FakePairingBackend.label)
        return get_user_model().objects.create_user(username="pairing-owner", email="pairing@example.com")


@pytest.fixture
def cross_process_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let reset and session-liveness checks trust the lock backend."""

    monkeypatch.setattr(live_module, "task_locks_are_cross_process", lambda: True)
    monkeypatch.setattr(connect, "task_locks_are_cross_process", lambda: True)


def test_unfinished_pairing_scopes_empty_unlinked_channels_newest_first(pairing_user: Any) -> None:
    """Selection composes owner, backend, messages, lifecycle, and the durable identity claim."""

    first = _channel(pairing_user, "pairing-first", lifecycle=IntegrationLifecycle.DISCONNECTED)
    second = _channel(pairing_user, "pairing-second")
    third = _channel(pairing_user, "pairing-third")
    _channel(pairing_user, "pairing-paused", lifecycle=IntegrationLifecycle.PAUSED)
    _channel(pairing_user, "pairing-other-backend", backend_class="manual")
    make_integration("pairing-other-owner", model=Channel, backend_class=FakePairingBackend.key)
    durable = _channel(pairing_user, "pairing-durable-identity")
    blank = _channel(pairing_user, "pairing-blank-identity")
    rejected = _channel(pairing_user, "pairing-rejected-identity")
    populated = _channel(pairing_user, "pairing-with-message")

    with system_context(reason="test.messaging.connect.selection"):
        earlier = timezone.now() - timedelta(days=1)
        Channel.objects.filter(pk__in=[first.pk, third.pk]).update(created_at=earlier)
        Channel.objects.filter(pk=second.pk).update(created_at=earlier + timedelta(seconds=1))
        durable.merge_subscription_state(own_id="durable-account")
        blank.merge_subscription_state(own_id="")
        # A duplicate rejection releases the claim but keeps the reported id.
        rejected.sync_progress = {"details": {"pairing": {"state": "duplicate_account", "own_id": "rejected-account"}}}
        rejected.save(update_fields=["sync_progress", "updated_at"])
        # A message remains channel-owned even without a thread.
        Message.objects.create(channel=populated, external_id="received-message", created_by=pairing_user)

        unfinished = Channel.objects.unfinished_pairing(pairing_user, FakePairingBackend.key)

        assert list(unfinished) == [rejected, blank, second, third, first]
        assert list(unfinished.filter(pk=first.pk)) == [first]
        assert list(Channel.objects.filter(pk=third.pk).unfinished_pairing(pairing_user, FakePairingBackend.key)) == [
            third
        ]


def test_resume_or_create_channel_creates_and_starts_when_none_is_unfinished(
    pairing_user: Any,
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """A new connection uses the shared disconnected factory and starts its session."""

    channel = connect.resume_or_create_channel(
        pairing_user,
        name="New connection",
        backend_class=FakePairingBackend.key,
    )

    with system_context(reason="test.messaging.connect.create.verify"):
        channel.refresh_from_db()
        assert Channel.objects.count() == 1
        assert channel.owner_id == pairing_user.pk
        assert channel.created_by_id == pairing_user.pk
        assert channel.display_name == "New connection"
        assert channel.backend_class == FakePairingBackend.key
        assert channel.lifecycle == IntegrationLifecycle.CONNECTED
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
        assert connect.channel_pairing(channel).state == PairingState.STARTING
    assert [(entry["name"], entry["kwargs"]["pk"]) for entry in pairing_graphql] == [(RUN_SESSION_TASK, channel.pk)]


@pytest.mark.usefixtures("cross_process_locks")
def test_resume_or_create_channel_resets_preexisting_store_after_identity_was_cleared(
    pairing_user: Any,
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """A rejected account can leave linked material in a pre-existing whole-directory store."""

    older = _channel(pairing_user, "pairing-older-abandoned")
    rejected = _channel(pairing_user, "pairing-rejected-account")
    with system_context(reason="test.messaging.connect.rejected.seed"):
        # Duplicate-account rejection releases the claim (the report keeps the
        # rejected id) but cannot discard linked material in a store that the
        # session did not create.
        rejected.merge_subscription_state(desired=Channel.LiveState.STOPPED)
        rejected.runtime_status = IntegrationRuntimeStatus.ERROR
        rejected.sync_progress = {"details": {"pairing": {"state": "duplicate_account", "own_id": "rejected-account"}}}
        rejected.save(update_fields=["runtime_status", "sync_progress", "updated_at"])
        rejected.credential.update_material(password="abandoned-pairing-secret")
    store = session_store_path(rejected)
    (store / "accounts").mkdir(parents=True)
    (store / "accounts" / "linked-account.db").write_bytes(b"rejected-linked-account")
    older_store = session_store_path(older)
    older_store.mkdir(parents=True)
    (older_store / "session.db").write_bytes(b"older-session")

    reused = connect.resume_or_create_channel(
        pairing_user,
        name="New connection",
        backend_class=FakePairingBackend.key,
    )

    assert reused.pk == rejected.pk
    assert not store.exists()
    assert (older_store / "session.db").read_bytes() == b"older-session"
    with system_context(reason="test.messaging.connect.rejected.verify"):
        reused.refresh_from_db()
        assert Channel.objects.count() == 2
        assert reused.display_name == rejected.display_name
        assert reused.lifecycle == IntegrationLifecycle.CONNECTED
        assert reused.runtime_status == IntegrationRuntimeStatus.OK
        assert reused.subscription_state["desired"] == Channel.LiveState.LIVE
        assert "own_id" not in reused.subscription_state
        assert "pairing" not in reused.sync_progress.get("details", {})
        assert "password" not in reused.credential.reveal()
        pairing = connect.channel_pairing(reused)
        assert pairing.state == PairingState.STARTING
        assert pairing.qr == ""
    assert [(entry["name"], entry["kwargs"]["pk"]) for entry in pairing_graphql] == [(RUN_SESSION_TASK, rejected.pk)]


def test_resume_or_create_channel_creates_instead_of_unprovable_reset_on_process_local_locks(
    pairing_user: Any,
    pairing_graphql: list[dict[str, Any]],
) -> None:
    """A process-local lock backend cannot prove a reset safe, so a stored channel is left alone."""

    abandoned = _channel(pairing_user, "pairing-abandoned-with-store")
    store = session_store_path(abandoned)
    store.mkdir(parents=True)
    (store / "session.db").write_bytes(b"abandoned-session")

    channel = connect.resume_or_create_channel(
        pairing_user, name="New connection", backend_class=FakePairingBackend.key
    )

    assert channel.pk != abandoned.pk
    assert (store / "session.db").read_bytes() == b"abandoned-session"
    with system_context(reason="test.messaging.connect.local_locks.verify"):
        assert Channel.objects.count() == 2
    assert [(entry["name"], entry["kwargs"]["pk"]) for entry in pairing_graphql] == [(RUN_SESSION_TASK, channel.pk)]


@pytest.mark.usefixtures("cross_process_locks")
def test_resume_or_create_channel_surfaces_shutdown_error_without_creating_another_channel(
    pairing_user: Any,
    pairing_graphql: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreleased session keeps its store and a retry does not multiply channels."""

    channel = _channel(pairing_user, "pairing-still-running")
    store = session_store_path(channel)
    store.mkdir(parents=True)
    (store / "session.db").write_bytes(b"running-session")

    def still_running(_channel: Any) -> None:
        raise TimeoutError("The session has not exited.")

    monkeypatch.setattr(connect, "await_session_exit", still_running)

    with pytest.raises(connect.PairingActionError, match="still shutting down"):
        connect.resume_or_create_channel(
            pairing_user,
            name="New connection",
            backend_class=FakePairingBackend.key,
        )

    assert (store / "session.db").read_bytes() == b"running-session"
    with system_context(reason="test.messaging.connect.shutdown.verify"):
        assert Channel.objects.count() == 1
        channel.refresh_from_db()
        assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
    assert pairing_graphql == []


def test_resume_or_create_channel_resumes_storeless_channel_without_reset(
    pairing_user: Any,
    pairing_graphql: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to wipe means no reset, so process-local lock backends keep working."""

    channel = _channel(pairing_user, "pairing-never-started")

    def unexpected_reset(_channel: Any) -> None:
        raise AssertionError("A storeless channel must not be reset.")

    monkeypatch.setattr(connect, "reset_channel_pairing", unexpected_reset)

    reused = connect.resume_or_create_channel(pairing_user, name="Ignored", backend_class=FakePairingBackend.key)

    assert reused.pk == channel.pk
    with system_context(reason="test.messaging.connect.storeless.verify"):
        reused.refresh_from_db()
        assert Channel.objects.count() == 1
        assert reused.display_name == "pairing-never-started"
        assert reused.subscription_state["desired"] == Channel.LiveState.LIVE
    assert [(entry["name"], entry["kwargs"]["pk"]) for entry in pairing_graphql] == [(RUN_SESSION_TASK, channel.pk)]


def test_resume_or_create_channel_rejects_poll_only_backend_without_creating(pairing_user: Any) -> None:
    """A non-live backend fails before any channel row exists."""

    with pytest.raises(connect.PairingActionError, match="live channel"):
        connect.resume_or_create_channel(pairing_user, name="Manual", backend_class="manual")

    with system_context(reason="test.messaging.connect.poll_only.verify"):
        assert not Channel.objects.exists()


@pytest.mark.usefixtures("cross_process_locks")
def test_resume_channel_pairing_keeps_report_of_running_session(
    pairing_user: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running session owns its live prompt; a direct resume must not hide it."""

    channel = _channel(pairing_user, "pairing-running")
    report = {"state": "awaiting_scan", "qr": "data:image/png;base64,live-qr"}
    with system_context(reason="test.messaging.connect.running.seed"):
        channel.sync_progress = {"details": {"pairing": report}}
        channel.save(update_fields=["sync_progress", "updated_at"])
    monkeypatch.setattr(Channel, "is_syncing", property(lambda _self: True))

    connect.resume_channel_pairing(channel)

    with system_context(reason="test.messaging.connect.running.verify"):
        channel.refresh_from_db()
        assert channel.sync_progress["details"]["pairing"] == report


def test_resume_channel_pairing_keeps_report_when_locks_cannot_prove_liveness(pairing_user: Any) -> None:
    """A process-local lock backend cannot see a worker's session, so its report stays."""

    channel = _channel(pairing_user, "pairing-unprovable")
    report = {"state": "awaiting_scan", "qr": "data:image/png;base64,maybe-live-qr"}
    with system_context(reason="test.messaging.connect.unprovable.seed"):
        channel.sync_progress = {"details": {"pairing": report}}
        channel.save(update_fields=["sync_progress", "updated_at"])

    connect.resume_channel_pairing(channel)

    with system_context(reason="test.messaging.connect.unprovable.verify"):
        channel.refresh_from_db()
        assert channel.sync_progress["details"]["pairing"] == report


@pytest.mark.usefixtures("cross_process_locks")
@pytest.mark.parametrize("own_id", ["", "linked-account"])
def test_resume_channel_pairing_clears_stale_qr_without_releasing_identity(
    pairing_user: Any,
    own_id: str,
) -> None:
    """Resume drops the transient report while preserving durable identity and unrelated progress."""

    channel = _channel(pairing_user, "pairing-stale-qr")
    with system_context(reason="test.messaging.connect.resume.seed"):
        channel.merge_subscription_state(desired=Channel.LiveState.LIVE, own_id=own_id)
        channel.sync_progress = {
            "details": {
                "pairing": {"state": "awaiting_scan", "qr": "data:image/png;base64,dead-qr"},
                "items": 7,
            }
        }
        channel.save(update_fields=["sync_progress", "updated_at"])
        if not own_id:
            assert connect.channel_pairing(channel).qr == "data:image/png;base64,dead-qr"

    connect.resume_channel_pairing(channel)

    with system_context(reason="test.messaging.connect.resume.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["own_id"] == own_id
        assert channel.sync_progress["details"] == {"items": 7}
        pairing = connect.channel_pairing(channel)
        assert pairing.qr == ""
        assert pairing.own_id == own_id
        assert pairing.state == (PairingState.PAIRED if own_id else PairingState.STARTING)


def _channel(user: Any, slug: str, **values: Any) -> Any:
    """Create one credential-backed live channel belonging to the selected user."""

    return make_integration(
        slug,
        model=Channel,
        owner=user,
        display_name=slug,
        **{"backend_class": FakePairingBackend.key, "lifecycle": IntegrationLifecycle.CONNECTED, **values},
    )
