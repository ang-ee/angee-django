"""Tests for the generic live bridge/session runtime."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field, replace
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
from angee.integrate.session import PASSWORD_SKIPPED, LiveSession, PasswordSkipped
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

    def _download(self, payload: Any, fact: Any) -> bytes | None:
        return None

    def _handle(self, kind: str, payload: Any) -> bool:
        return self._still_wanted()


class PasswordInputSession(FakeLiveSession):
    """Fake vendor thread that requests one password and reports sign-in success."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.received_password: str | None = None
        self.password_received = threading.Event()

    def _connect(self) -> None:
        self.received_password = self.request_password("Enter the account password.")
        self.password_received.set()
        if self.received_password is not None:
            self.events.put(("paired", "account-1"))
            self.events.put(("disconnected", None))
            self._stopping.wait(timeout=1)

    def _shutdown(self, connection: threading.Thread) -> bool:
        connection.join(timeout=0.5)
        return not connection.is_alive()


class RePromptingPasswordInputSession(FakeLiveSession):
    """Fake vendor that rejects one password before accepting the next round."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.received_passwords: list[str | None] = []
        self.first_password_received = threading.Event()
        self.second_password_received = threading.Event()

    def _connect(self) -> None:
        first = self.request_password("Enter the account password.")
        self.received_passwords.append(first)
        self.first_password_received.set()
        if first is None:
            return
        second = self.request_password("Wrong password, try again.")
        self.received_passwords.append(second)
        self.second_password_received.set()
        if second is not None:
            self.events.put(("paired", "account-1"))
            self.events.put(("disconnected", None))
            self._stopping.wait(timeout=1)

    def _shutdown(self, connection: threading.Thread) -> bool:
        connection.join(timeout=0.5)
        return not connection.is_alive()


class OptionalPasswordInputSession(FakeLiveSession):
    """Fake vendor that requests a skippable secret under a non-password key."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.received_password: str | PasswordSkipped | None = None
        self.password_received = threading.Event()

    def _connect(self) -> None:
        self.received_password = self.request_password(
            "Enter the recovery key.",
            material_key="recovery_key",
            optional=True,
        )
        self.password_received.set()
        if self.received_password is not None:
            self.events.put(("paired", "account-1"))
            self.events.put(("disconnected", None))
            self._stopping.wait(timeout=1)

    def _shutdown(self, connection: threading.Thread) -> bool:
        connection.join(timeout=0.5)
        return not connection.is_alive()


class FakeLiveChannelBackend(LiveChannelBackend):
    """Live backend with no vendor dependency, used by generic integrate tests."""

    key = "whatsapp"
    label = "Fake Live"
    session_queue = "fake-live"
    session_class: ClassVar[Any] = "tests.test_integrate_live.FakeLiveSession"

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass already-neutral fake messages through unchanged."""

        return message


@dataclass(frozen=True)
class _QueuedLiveMessage:
    """Vendor-shaped queued DTO for the default media-attachment hook."""

    metadata: dict[str, Any] = field(default_factory=dict)
    media: tuple[Any, ...] = ()

    def with_media(self, media: tuple[Any, ...]) -> _QueuedLiveMessage:
        """Return the vendor DTO with its resolved media attached."""

        return replace(self, media=media)


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


def _wait_until(predicate: Any, *, timeout: float = 1.0) -> None:
    """Wait until a cross-thread session assertion becomes true."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


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
def test_live_session_hands_off_submitted_password_and_consumes_it_once(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task thread delivers encrypted material to the waiting vendor thread once."""

    from angee.integrate import session as session_module
    from angee.integrate.sync import BridgeProgressReporter
    from angee.messaging.connect import submit_channel_password

    monkeypatch.setattr(session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    channel = _live_channel("fake-live-password")
    session = PasswordInputSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    password = "one-use-secret"
    submitted_material: list[str] = []

    def submit_password() -> None:
        _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
        with system_context(reason="test live password operator"):
            operator_channel = type(channel).objects.get(pk=channel.pk)
            submit_channel_password(operator_channel, password)
            submitted_material.append(operator_channel.credential.reveal()["password"])

    operator_thread = threading.Thread(target=submit_password, daemon=True)

    with system_context(reason="test live password handoff"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        session_credential = channel.credential
        assert "password" not in session_credential.reveal()
        operator_thread.start()
        outcome = session.run()
        operator_thread.join(timeout=1)
        assert not operator_thread.is_alive()
        assert channel.subscription_state["awaiting"] == ""
        assert channel.sync_progress["details"]["pairing"] == {
            "state": PairingState.PAIRED,
            "own_id": "account-1",
            "account_label": "account-1",
        }
        assert submitted_material == [password]
        assert session.password_received.is_set()
        assert session.received_password == password

    assert outcome is PairingState.PAIRED
    with system_context(reason="test live password handoff verify"):
        fresh_credential = type(session_credential).objects.get(pk=session_credential.pk)
        assert "password" not in fresh_credential.reveal()
        observer = type(channel).objects.get(pk=channel.pk)
        assert password not in str(observer.subscription_state)
        assert password not in str(observer.sync_progress)


@pytest.mark.django_db(transaction=True)
def test_live_session_reports_the_non_secret_password_prompt(live_tables: Any) -> None:
    """The awaiting report carries the vendor prompt but never a password value."""

    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel("fake-live-password-prompt")
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )

    with system_context(reason="test live password prompt"):
        session._mark_awaiting_password("Enter the account password.")

    assert channel.subscription_state["awaiting"] == "password"
    assert channel.sync_progress["details"]["pairing"] == {
        "state": PairingState.AWAITING_PASSWORD,
        "message": "Enter the account password.",
    }


@pytest.mark.django_db(transaction=True)
def test_optional_material_round_skips_without_touching_durable_password(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-password round carries its key, projects skip, and returns the sentinel."""

    from angee.integrate import session as session_module
    from angee.integrate.sync import BridgeProgressReporter
    from angee.messaging.connect import skip_channel_password

    monkeypatch.setattr(session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    channel = _live_channel("fake-live-recovery-skip")
    channel.credential.update_material(password="durable-login-password")
    session = OptionalPasswordInputSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    operator_failures: list[BaseException] = []

    def skip_round() -> None:
        try:
            _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
            with system_context(reason="test optional material skip operator"):
                operator_channel = type(channel).objects.get(pk=channel.pk)
                assert operator_channel.subscription_state["awaiting"] == "recovery_key"
                assert operator_channel.live_impl.pairing().can_skip is True
                skip_channel_password(operator_channel)
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            operator_failures.append(error)

    operator_thread = threading.Thread(target=skip_round, daemon=True)
    with system_context(reason="test optional material skip"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator_thread.start()
        outcome = session.run()
        operator_thread.join(timeout=1)
        assert not operator_thread.is_alive()

    assert operator_failures == []
    assert outcome is PairingState.PAIRED
    assert session.password_received.is_set()
    assert session.received_password is PASSWORD_SKIPPED
    with system_context(reason="test optional material skip verify"):
        credential = type(channel.credential).objects.get(pk=channel.credential_id)
        assert credential.reveal()["password"] == "durable-login-password"
        assert "recovery_key" not in credential.reveal()


@pytest.mark.django_db(transaction=True)
def test_credential_update_material_merges_stale_writers_and_deletes_none(live_tables: Any) -> None:
    """Credential material edits re-read under lock and encode through one owner."""

    channel = _live_channel("fake-live-material-merge")
    with system_context(reason="test credential material merge"):
        first = channel.credential
        second = type(first).objects.get(pk=first.pk)
        original = first.reveal()

        first.update_material(password="round-1")
        second.update_material(other_transient="preserved")

        fresh = type(first).objects.get(pk=first.pk)
        assert fresh.reveal() == {
            **original,
            "other_transient": "preserved",
            "password": "round-1",
        }
        assert fresh.material == type(fresh).encode_material(fresh.reveal())

        second.update_material(password=None)

        fresh = type(first).objects.get(pk=first.pk)
        assert fresh.reveal() == {**original, "other_transient": "preserved"}
        assert "password" not in fresh.reveal()


@pytest.mark.django_db(transaction=True)
def test_password_rearm_scrubs_stale_material_and_queued_answers(live_tables: Any) -> None:
    """A new prompt round invalidates both persisted and in-memory old answers."""

    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel("fake-live-password-rearm")
    channel.credential.update_material(password="abandoned-secret")
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.inputs.put("late-answer")
    session._password_delivered = True

    with system_context(reason="test live password rearm"):
        session._mark_awaiting_password("Try again.")

    with pytest.raises(queue.Empty):
        session.inputs.get_nowait()
    assert session._password_delivered is False
    with system_context(reason="test live password rearm verify"):
        fresh_credential = type(channel.credential).objects.get(pk=channel.credential_id)
        assert "password" not in fresh_credential.reveal()
    assert "abandoned-secret" not in str(channel.subscription_state)
    assert "late-answer" not in str(channel.sync_progress)


@pytest.mark.django_db(transaction=True)
def test_live_session_end_discards_queued_password_answers(live_tables: Any) -> None:
    """A stopped session cannot leave an answer queued for a later session round."""

    from angee.integrate.sync import BridgeProgressReporter

    channel = _live_channel("fake-live-password-end")
    stop_event = threading.Event()
    stop_event.set()
    session = FakeLiveSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=stop_event,
    )
    session.inputs.put("late-answer")
    session._password_delivered = True

    with system_context(reason="test live password end"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session.run() is PairingState.STOPPED

    with pytest.raises(queue.Empty):
        session.inputs.get_nowait()
    assert session._password_delivered is False


@pytest.mark.django_db(transaction=True)
def test_second_round_resubmit_delivers_the_new_password(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected first password cannot satisfy the vendor's second prompt."""

    from angee.integrate import session as session_module
    from angee.integrate.sync import BridgeProgressReporter
    from angee.messaging.connect import submit_channel_password

    monkeypatch.setattr(session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    channel = _live_channel("fake-live-password-round2")
    with system_context(reason="test second password round load"):
        channel = type(channel).objects.get(pk=channel.pk)
    session = RePromptingPasswordInputSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    operator_failures: list[BaseException] = []

    def submit_both_rounds() -> None:
        try:
            _wait_until(
                lambda: (
                    session.pairing is PairingState.AWAITING_PASSWORD
                    and channel.sync_progress.get("details", {}).get("pairing", {}).get("message")
                    == "Enter the account password."
                )
            )
            with system_context(reason="test first password operator"):
                first_operator_channel = type(channel).objects.get(pk=channel.pk)
                submit_channel_password(first_operator_channel, "wrong-password-1")
            assert session.first_password_received.wait(timeout=0.5)
            _wait_until(
                lambda: (
                    channel.sync_progress.get("details", {}).get("pairing", {}).get("message")
                    == "Wrong password, try again."
                )
            )
            with system_context(reason="test second password operator"):
                second_operator_channel = type(channel).objects.get(pk=channel.pk)
                submit_channel_password(second_operator_channel, "correct-password-2")
                assert second_operator_channel.credential.reveal()["password"] == "correct-password-2"
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            operator_failures.append(error)

    operator_thread = threading.Thread(target=submit_both_rounds, daemon=True)

    with system_context(reason="test second password round"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator_thread.start()
        outcome = session.run()
        operator_thread.join(timeout=1)
        assert not operator_thread.is_alive()

    assert operator_failures == []
    assert outcome is PairingState.PAIRED
    assert session.received_passwords == ["wrong-password-1", "correct-password-2"]
    with system_context(reason="test second password round verify"):
        fresh_channel = type(channel).objects.get(pk=channel.pk)
        assert "password" not in fresh_channel.credential.reveal()
        assert "wrong-password-1" not in str(fresh_channel.subscription_state)
        assert "correct-password-2" not in str(fresh_channel.subscription_state)
        assert "wrong-password-1" not in str(fresh_channel.sync_progress)
        assert "correct-password-2" not in str(fresh_channel.sync_progress)


@pytest.mark.django_db(transaction=True)
def test_missing_credential_at_password_delivery_is_a_latched_session_outcome(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A credential removed after prompting ends and latches instead of raising."""

    from angee.integrate import session as session_module
    from angee.integrate import tasks as tasks_module
    from angee.integrate.sync import BridgeProgressReporter

    monkeypatch.setattr(session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    channel = _live_channel("fake-live-password-missing-credential")
    session = PasswordInputSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    operator_failures: list[BaseException] = []

    def remove_credential_and_signal_ready() -> None:
        try:
            _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
            with system_context(reason="test remove password credential"):
                operator_channel = type(channel).objects.get(pk=channel.pk)
                operator_channel.credential = None
                operator_channel.save(update_fields=["credential", "updated_at"])
                operator_channel.merge_subscription_state(awaiting="")
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            operator_failures.append(error)

    operator_thread = threading.Thread(target=remove_credential_and_signal_ready, daemon=True)
    with system_context(reason="test missing password credential"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator_thread.start()
        outcome = session.run()
        operator_thread.join(timeout=1)
        assert not operator_thread.is_alive()

    assert operator_failures == []
    assert outcome is PairingState.STOPPED
    with system_context(reason="test missing password credential verify"):
        fresh_channel = type(channel).objects.get(pk=channel.pk)
        assert fresh_channel.runtime_status == IntegrationRuntimeStatus.ERROR
        assert fresh_channel.sync_stage == fresh_channel.SyncStage.FAILED
        assert fresh_channel.sync_progress["details"]["pairing"]["state"] == PairingState.STOPPED
        assert "live bridge has no credential" in fresh_channel.sync_error.lower()
    assert "live bridge has no credential" in caplog.text.lower()
    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 0}


@pytest.mark.django_db(transaction=True)
def test_missing_password_at_consume_time_reports_and_rearms(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A readiness marker without material re-prompts and latches without raising."""

    from angee.integrate import session as session_module
    from angee.integrate import tasks as tasks_module
    from angee.integrate.sync import BridgeProgressReporter

    monkeypatch.setattr(session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    channel = _live_channel("fake-live-password-missing-material")
    stop_event = threading.Event()
    session = PasswordInputSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=stop_event,
    )
    operator_failures: list[BaseException] = []

    def signal_ready_without_material() -> None:
        try:
            _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
            with system_context(reason="test signal missing password material"):
                operator_channel = type(channel).objects.get(pk=channel.pk)
                operator_channel.merge_subscription_state(awaiting="")
            _wait_until(lambda: channel.runtime_status == IntegrationRuntimeStatus.ERROR)
            stop_event.set()
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            operator_failures.append(error)
            stop_event.set()

    operator_thread = threading.Thread(target=signal_ready_without_material, daemon=True)
    with system_context(reason="test missing password material"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator_thread.start()
        outcome = session.run()
        operator_thread.join(timeout=1)
        assert not operator_thread.is_alive()

    assert operator_failures == []
    assert outcome is PairingState.STOPPED
    with system_context(reason="test missing password material verify"):
        fresh_channel = type(channel).objects.get(pk=channel.pk)
        assert fresh_channel.runtime_status == IntegrationRuntimeStatus.ERROR
        assert fresh_channel.sync_stage == fresh_channel.SyncStage.FAILED
        assert fresh_channel.subscription_state["awaiting"] == "password"
        assert fresh_channel.sync_progress["details"]["pairing"] == {
            "state": PairingState.AWAITING_PASSWORD,
            "message": "The submitted password was unavailable. Enter the bridge password again.",
        }
        assert "no submitted password" in fresh_channel.sync_error.lower()
    assert "no submitted password" in caplog.text.lower()
    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 0}


@pytest.mark.django_db(transaction=True)
def test_live_session_stop_while_awaiting_password_exits_without_input(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted stop request unwinds a vendor thread blocked on password input."""

    from angee.integrate import session as session_module
    from angee.integrate.sync import BridgeProgressReporter

    monkeypatch.setattr(session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    channel = _live_channel("fake-live-password-stop")
    session = PasswordInputSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    connection_thread = threading.Thread(target=session._connect, daemon=True)

    with system_context(reason="test live password stop"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        connection_thread.start()
        assert session._drain_once() is True
        assert session.pairing == PairingState.AWAITING_PASSWORD

        channel.merge_subscription_state(desired=Channel.LiveState.STOPPED)
        assert session._drain_once() is False
        assert session.pairing == PairingState.STOPPED

    assert session._shutdown(connection_thread) is True
    assert session.password_received.is_set()
    assert session.received_password is None
    channel.credential.refresh_from_db()
    assert "password" not in channel.credential.reveal()


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
def test_live_channel_media_resolution_downloads_once_per_fact(
    live_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base passes each media fact without mutating a frozen DTO's metadata."""

    from angee.integrate.sync import BridgeProgressReporter
    from angee.messaging import backends as messaging_backends
    from angee.messaging.session import LiveChannelSession

    assert hasattr(messaging_backends, "MediaItem")
    media_item_class = messaging_backends.MediaItem
    channel = _live_channel("fake-live-media-owner")
    session = LiveChannelSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    first = media_item_class(mime="image/jpeg", name="photo.jpg")
    second = media_item_class(mime="image/png", name="diagram.png")
    original_metadata = {"_media_facts": (first, second), "kept": "value"}
    queued = _QueuedLiveMessage(metadata=original_metadata)
    downloads: list[tuple[Any, Any]] = []

    def download(payload: Any, fact: Any) -> bytes:
        downloads.append((payload, fact))
        return fact.name.encode()

    monkeypatch.setattr(session, "_download", download)

    resolved = session._with_media(queued, "wire")

    assert FakeLiveChannelBackend.media_item_class is media_item_class
    assert queued.metadata is original_metadata
    assert queued.metadata == {"_media_facts": (first, second), "kept": "value"}
    assert resolved.metadata == {"kept": "value"}
    assert downloads == [("wire", first), ("wire", second)]
    assert resolved.media == (
        media_item_class(mime="image/jpeg", name="photo.jpg", content=b"photo.jpg"),
        media_item_class(mime="image/png", name="diagram.png", content=b"diagram.png"),
    )


@pytest.mark.django_db(transaction=True)
def test_vendor_manager_resolves_seeded_slug_with_load_guidance(live_tables: Any) -> None:
    """The catalogue owner centralizes seeded lookup and slug-specific guidance."""

    from tests.conftest import Vendor

    with system_context(reason="test seeded vendor manager"):
        seeded = Vendor.objects.create(slug="seeded-owner", display_name="Seeded Owner")
        assert Vendor.objects.seeded("seeded-owner") == seeded
        with pytest.raises(
            ImproperlyConfigured,
            match=r"Vendor 'missing-owner'.*manage\.py resources load",
        ):
            Vendor.objects.seeded("missing-owner")


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
