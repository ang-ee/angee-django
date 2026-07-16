"""Tests for the generic live bridge/session runtime."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.integrate.live import PairingState
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import IntegrationRuntimeStatus
from angee.integrate.session import LiveSession
from angee.messaging.backends import LiveChannelBackend, ParsedMessage, ParsedPart, ParsedThread
from angee.tasks.locks import task_lock_is_held
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.test_messaging import MESSAGING_TEST_MODELS, Message
from tests.test_messaging_graphql import Channel

LIVE_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)


class FakeLiveSession(LiveSession):
    """Worker-only fake selected through a real dotted session-class path."""

    def _build_client(self, store: Path) -> Any:
        return object()

    def _connect(self) -> None:
        return None

    def _shutdown(self, connection: threading.Thread) -> bool:
        return True

    def _download(self, payload: Any) -> bytes | None:
        return None

    def _handle(self, kind: str, payload: Any) -> bool:
        return self._still_wanted()


class FakeLiveChannelBackend(LiveChannelBackend):
    """Live backend with no vendor dependency, used by generic integrate tests."""

    key = "whatsapp"
    label = "Fake Live"
    session_queue = "fake-live"
    session_class: ClassVar[Any] = "tests.test_integrate_live.FakeLiveSession"

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass already-neutral fake messages through unchanged."""

        return message


@pytest.fixture
def live_tables(settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create concrete messaging tables and register the fake live backend."""

    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    settings.ANGEE_CHANNEL_BACKEND_CLASSES = {
        **settings.ANGEE_CHANNEL_BACKEND_CLASSES,
        "whatsapp": "tests.test_integrate_live.FakeLiveChannelBackend",
    }
    monkeypatch.setattr("angee.integrate.tasks.bridge_models", lambda _base: (Channel,))
    created_models = _create_missing_tables(LIVE_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(LIVE_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _live_channel(slug: str = "fake-live") -> Any:
    """Create a connected, live-desired fake Channel row."""

    channel = make_integration(
        slug,
        model=Channel,
        backend_class="whatsapp",
        lifecycle="connected",
    )
    with system_context(reason="test fake live channel setup"):
        channel.subscription_state["desired"] = Channel.LiveState.LIVE
        channel.save(update_fields=["subscription_state", "updated_at"])
    return channel


@pytest.mark.django_db(transaction=True)
def test_live_session_wake_honors_persisted_stop_desire(live_tables: Any) -> None:
    """The bounded wake reads the base-owned desire — a stop ends the session."""

    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel()
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    with system_context(reason="test live desire check"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session._still_wanted() is True
        row = Channel._base_manager.get(pk=channel.pk)
        row.subscription_state["desired"] = Channel.LiveState.STOPPED
        row.save(update_fields=["subscription_state", "updated_at"])
        assert session._still_wanted() is False
        assert session.pairing == PairingState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_live_session_wake_honors_paused_lifecycle(live_tables: Any) -> None:
    """A generic pause stops a live session without clearing its account identity."""

    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel()
    with system_context(reason="test live pause setup"):
        channel.merge_subscription_state(own_id="account-1")
        channel.pause()
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    with system_context(reason="test live pause check"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session._still_wanted() is False
        assert session.pairing == PairingState.PAUSED


@pytest.mark.django_db(transaction=True)
def test_live_session_lost_lock_exits_for_clean_restart(live_tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dropped advisory lock ends the session instead of racing a duplicate."""

    from angee.integrate import session as session_module
    from angee.integrate.sync import BridgeProgressReporter

    monkeypatch.setattr(session_module, "WAKE_SECONDS", 0.05)
    monkeypatch.setattr(session_module, "bridge_is_locked", lambda _bridge: False)
    channel = _live_channel()
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    with system_context(reason="test live lost lock"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session.run() == PairingState.STARTING


@pytest.mark.django_db(transaction=True)
def test_live_session_worker_shutdown_exits_within_one_wake(live_tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm worker shutdown ends an idle live session without waiting on the socket."""

    from angee.integrate import session as session_module
    from angee.integrate.sync import BridgeProgressReporter

    monkeypatch.setattr(session_module, "WAKE_SECONDS", 0.05)
    channel = _live_channel()
    stop_event = threading.Event()
    stop_event.set()
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=stop_event,
    )
    with system_context(reason="test live worker shutdown"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session.run() == PairingState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_live_session_store_wipe_requires_released_connection(live_tables: Any) -> None:
    """A void store is wiped only after the vendor connection released it."""

    from angee.integrate.live import reset_session_store, session_store_path
    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel()
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"still-open")
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )

    session.discard_store()
    assert (store / "session.db").read_bytes() == b"still-open"

    session.store_released = True
    session.discard_store()
    assert not store.exists()

    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"explicit-reset")
    reset_session_store(channel)
    assert not store.exists()


@pytest.mark.django_db(transaction=True)
def test_run_bridge_session_records_logged_out_before_releasing_account(
    live_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A logged-out live session records runtime failure, then clears live desire."""

    from angee.integrate import tasks as tasks_module
    from angee.integrate.live import SessionLoggedOut, session_store_path

    events: list[str] = []

    class LoggedOutSession(FakeLiveSession):
        def run(self) -> Any:
            self.store_released = True
            events.append("recordable-error")
            raise SessionLoggedOut("The linked account removed this session.")

    monkeypatch.setattr(FakeLiveChannelBackend, "session_class", LoggedOutSession)
    channel = _live_channel()
    with system_context(reason="test live logout setup"):
        channel.merge_subscription_state(own_id="account-1")
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"invalid-device")

    result = tasks_module.run_bridge_session(channel._meta.label_lower, channel.pk)

    assert result == {"ok": False, "logged_out": True}
    channel.refresh_from_db()
    assert events == ["recordable-error"]
    assert channel.runtime_status == "error"
    assert channel.sync_stage == Channel.SyncStage.FAILED
    assert channel.next_sync_at is None
    assert channel.lifecycle == "connected"
    assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
    assert "own_id" not in channel.subscription_state
    assert not store.exists()


@pytest.mark.django_db(transaction=True)
def test_ensure_bridge_sessions_skips_poll_only_channels_before_desire_write(
    live_tables: Any,
) -> None:
    """A poll-only bridge may carry live desire facts but has no session queue."""

    from angee.integrate import tasks as tasks_module

    channel = make_integration(
        "manual-live-skip",
        model=Channel,
        backend_class="manual",
        lifecycle="connected",
    )
    with system_context(reason="test manual live skip setup"):
        channel.subscription_state["desired"] = Channel.LiveState.STOPPED
        channel.save(update_fields=["subscription_state", "updated_at"])

    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 0}

    channel.refresh_from_db()
    assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_ensure_bridge_sessions_reconciles_live_desire_and_routes_to_session_queue(
    live_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy connected live bridge gets live desire and a queued session."""

    from angee.integrate import tasks as tasks_module
    from angee.integrate.constants import RUN_SESSION_TASK, SESSION_START_EXPIRES

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.integrate.impl.enqueue_task",
        lambda name, *, kwargs, queue=None, expires=None, **_: sent.append(
            {"name": name, "kwargs": kwargs, "queue": queue, "expires": expires}
        ),
    )
    channel = make_integration(
        "fake-live-reconcile",
        model=Channel,
        backend_class="whatsapp",
        lifecycle="connected",
    )
    with system_context(reason="test fake live reconcile setup"):
        channel.stop_live()

    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 1}

    channel.refresh_from_db()
    assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
    assert sent == [
        {
            "name": RUN_SESSION_TASK,
            "kwargs": {"model_label": channel._meta.label_lower, "pk": channel.pk},
            "queue": "fake-live",
            "expires": SESSION_START_EXPIRES,
        }
    ]


@pytest.mark.django_db(transaction=True)
def test_ensure_bridge_sessions_latches_runtime_error_until_resume(
    live_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed handshake stays quiet until the operator's resume clears it."""

    from angee.integrate import tasks as tasks_module
    from angee.messaging.connect import resume_channel_pairing

    sent: list[str] = []
    monkeypatch.setattr(
        "angee.integrate.impl.enqueue_task",
        lambda name, **_: sent.append(name),
    )
    channel = _live_channel("fake-live-runtime-error")
    with system_context(reason="test live runtime error latch"):
        channel.runtime_status = IntegrationRuntimeStatus.ERROR
        channel.save(update_fields=["runtime_status", "updated_at"])

    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 0}
    assert sent == []

    resume_channel_pairing(channel)

    channel.refresh_from_db()
    assert channel.runtime_status == IntegrationRuntimeStatus.OK
    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 1}


def test_live_backend_holds_account_lock_key() -> None:
    """The account-scoped lock namespace follows the backend key."""

    backend = FakeLiveChannelBackend.__new__(FakeLiveChannelBackend)
    assert backend.account_lock_key("account-1").name == "angee:whatsapp-account:account-1"
    with backend.account_lock("account-1") as acquired:
        assert acquired
        assert task_lock_is_held(backend.account_lock_key("account-1"))


@pytest.mark.django_db(transaction=True)
def test_bridge_owns_live_contracts_and_resolves_one_live_impl(live_tables: Any) -> None:
    """The bridge row exposes its vocabularies and one declared impl accessor."""

    from angee.integrate.live import session_store_path
    from angee.integrate.models import Bridge, IntegrationLifecycle

    channel = _live_channel()

    assert Channel.LiveState is Bridge.LiveState
    assert Channel.Lifecycle is IntegrationLifecycle
    assert channel.live_impl_field == "backend_class"
    assert isinstance(channel.live_impl, FakeLiveChannelBackend)
    assert session_store_path(channel).parts[-2:] == ("whatsapp", str(channel.sqid))


@pytest.mark.django_db(transaction=True)
def test_live_backend_projects_neutral_pairing_fields(live_tables: Any) -> None:
    """The base projection exposes no vendor-shaped identity fields."""

    from angee.integrate.live import PairingProjection

    channel = _live_channel("fake-live-pairing")
    with system_context(reason="test neutral live pairing projection"):
        channel.merge_subscription_state(own_id="account-1")

    assert channel.live_impl.pairing() == PairingProjection(
        state=PairingState.PAIRED,
        own_id="account-1",
        account_label="account-1",
    )


@pytest.mark.django_db(transaction=True)
def test_live_session_routes_message_events_through_the_declared_handle_hook(live_tables: Any) -> None:
    """Integrate treats messaging event names as opaque worker-session input."""

    from angee.integrate.sync import BridgeProgressReporter

    handled: list[tuple[str, Any]] = []

    class RoutedSession(FakeLiveSession):
        def _handle(self, kind: str, payload: Any) -> bool:
            handled.append((kind, payload))
            return False

    payload = object()
    channel = _live_channel()
    session = RoutedSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.events.put(("messages", payload))

    assert session._drain_once() is False
    assert handled == [("messages", payload)]


@pytest.mark.django_db(transaction=True)
def test_live_channel_session_owns_message_ingest(live_tables: Any) -> None:
    """The messaging worker session lands queued messages through its public impl hooks."""

    from angee.integrate.sync import BridgeProgressReporter
    from angee.messaging.session import LiveChannelSession

    channel = _live_channel()
    session = LiveChannelSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    message = ParsedMessage(
        external_id="fake/message-1",
        platform="whatsapp",
        thread=ParsedThread(external_id="fake/thread-1"),
        body=ParsedPart(type="text/plain", role="body", text="hello"),
    )

    with system_context(reason="test live channel ingest"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session._handle("messages", [(message, None)]) is True

    assert Message._base_manager.filter(external_id="fake/message-1").exists()
    assert session.landed == 1


@pytest.mark.django_db(transaction=True)
def test_live_report_preserves_caller_supplied_identity(live_tables: Any) -> None:
    """A caller's transient identity value wins over the durable fallback."""

    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel()
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.own_id = "durable-account"

    with system_context(reason="test live report identity"):
        session._report(PairingState.PAIRED, own_id="caller-account")

    channel.refresh_from_db()
    assert channel.sync_progress["details"]["pairing"]["own_id"] == "caller-account"


@pytest.mark.django_db(transaction=True)
def test_live_backend_requires_a_dedicated_session_queue(live_tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A long-lived session may never fall through to the shared prefork queue."""

    class QueueLessBackend(FakeLiveChannelBackend):
        session_queue = ""

    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)
    with pytest.raises(ImproperlyConfigured, match="shared prefork queue"):
        QueueLessBackend(_live_channel()).start_live()


@pytest.mark.django_db(transaction=True)
def test_run_bridge_session_duplicate_discards_new_store_without_moving_lifecycle(
    live_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generic duplicate outcome releases runtime state and only its new store."""

    from angee.integrate import tasks as tasks_module
    from angee.integrate.live import session_store_path

    events: list[str] = []

    class DuplicateSession(FakeLiveSession):
        def run(self) -> PairingState:
            self.created_store = True
            self.store_released = True
            self.duplicate_error = RuntimeError("duplicate fake account")
            return PairingState.DUPLICATE_ACCOUNT

        def discard_new_store(self) -> None:
            events.append("discard-new-store")
            super().discard_new_store()

    monkeypatch.setattr(FakeLiveChannelBackend, "session_class", DuplicateSession)
    channel = _live_channel("fake-live-duplicate")
    store = session_store_path(channel)
    store.mkdir(parents=True, exist_ok=True)
    (store / "session.db").write_bytes(b"new pairing")

    assert tasks_module.run_bridge_session(channel._meta.label_lower, channel.pk) == {
        "ok": False,
        "duplicate_account": True,
    }

    channel.refresh_from_db()
    assert events == ["discard-new-store"]
    assert channel.lifecycle == Channel.Lifecycle.CONNECTED
    assert channel.runtime_status == "error"
    assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
    assert not store.exists()


@pytest.mark.django_db(transaction=True)
def test_ensure_bridge_sessions_contains_unresolvable_registered_impl(
    live_tables: Any,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One broken registry key is logged and excluded before any row resolves it."""

    from angee.integrate import tasks as tasks_module

    make_integration(
        "fake-live-broken",
        model=Channel,
        backend_class="manual",
        lifecycle="connected",
    )
    settings.ANGEE_CHANNEL_BACKEND_CLASSES = {
        **settings.ANGEE_CHANNEL_BACKEND_CLASSES,
        "manual": "tests.test_integrate_live.MissingBackend",
    }
    _live_channel("fake-live-after-broken")
    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)

    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 1}
    assert "manual" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_ensure_bridge_sessions_does_not_write_a_settled_live_row(
    live_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy settled tick dispatches without publishing a no-op row edit."""

    from angee.integrate import tasks as tasks_module

    channel = _live_channel("fake-live-settled")
    channel.refresh_from_db()
    settled_at = channel.updated_at
    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)

    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 1}

    channel.refresh_from_db()
    assert channel.updated_at == settled_at
