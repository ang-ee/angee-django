"""Tests for the WhatsApp channel addon.

Layered like the addon: (a) the pure identity/mapping rules in ``parser.py`` —
the convergence contract both ingest paths share — from literal wire shapes;
(b) the live session/task layer against a fake client seam; (c) the backup
importer against ChatStorage fixtures synthesized in-test. The backup+live
convergence regression (same chat/stanza → one row) is the load-bearing case:
it pins the property the whole design hangs on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from angee.messaging_integrate_whatsapp.parser import (
    ChatMessage,
    MediaItem,
    bare_jid,
    external_id,
    handle_for_jid,
    parsed_message,
    phone_for_jid,
)

UTC = timezone.utc


def test_bare_jid_strips_device_and_agent_qualifiers() -> None:
    """Live device-qualified JIDs and backup bare JIDs normalize to one identity."""

    assert bare_jid("4917000001@s.whatsapp.net") == "4917000001@s.whatsapp.net"
    assert bare_jid("4917000001:12@s.whatsapp.net") == "4917000001@s.whatsapp.net"
    assert bare_jid("4917000001.3:12@s.whatsapp.net") == "4917000001@s.whatsapp.net"
    assert bare_jid("4917000001-1600000000@g.us") == "4917000001-1600000000@g.us"
    assert bare_jid("Status@Broadcast") == "status@broadcast"
    assert bare_jid("") == ""
    assert bare_jid("not-a-jid") == "not-a-jid"


def test_phone_derives_only_from_individual_jids() -> None:
    """E.164 values come from phone-number user parts on the individual server."""

    assert phone_for_jid("4917000001:2@s.whatsapp.net") == "+4917000001"
    assert phone_for_jid("4917000001-1600000000@g.us") == ""
    assert phone_for_jid("123456789@lid") == ""


def test_handle_prefers_phone_value_and_keeps_jid_identity() -> None:
    """The handle's value is human-readable; the JID stays the stable identity."""

    individual = handle_for_jid("4917000001:9@s.whatsapp.net", "Ada")
    assert individual.value == "+4917000001"
    assert individual.external_id == "4917000001@s.whatsapp.net"
    assert individual.display_name == "Ada"
    assert individual.platform == "whatsapp"

    hidden = handle_for_jid("987654@lid")
    assert hidden.value == "987654@lid"
    assert hidden.external_id == "987654@lid"


def test_external_id_is_chat_scoped_and_normalized() -> None:
    """Stanza ids embed their (normalized) chat scope — the convergence key."""

    assert external_id("4917000001:3@s.whatsapp.net", "3EB0AF") == "4917000001@s.whatsapp.net/3EB0AF"


def test_parsed_message_maps_the_full_envelope() -> None:
    """Direction, sender, thread, reply reference, and metadata all land."""

    parsed = parsed_message(
        ChatMessage(
            chat_jid="4917000001-1600000000@g.us",
            stanza_id="ABCD",
            chat_name="Weekend plans",
            sender_jid="4917000002:5@s.whatsapp.net",
            sender_name="Bob",
            from_me=False,
            timestamp=datetime(2026, 7, 1, 9, 30, tzinfo=UTC),
            text="See you there",
            quoted_stanza_id="ZYXW",
        )
    )
    assert parsed.external_id == "4917000001-1600000000@g.us/ABCD"
    assert parsed.platform == "whatsapp"
    assert parsed.direction == "inbound"
    assert parsed.sender is not None and parsed.sender.value == "+4917000002"
    assert parsed.thread is not None
    assert parsed.thread.external_id == "4917000001-1600000000@g.us"
    assert parsed.thread.modality == "group"
    assert parsed.thread.title == "Weekend plans"
    assert parsed.in_reply_to == "4917000001-1600000000@g.us/ZYXW"
    assert parsed.body is not None and parsed.body.text == "See you there"
    assert parsed.metadata["chat_jid"] == "4917000001-1600000000@g.us"


def test_parsed_message_outbound_direct_and_fallback_id() -> None:
    """A from-me, stanza-less backup row lands outbound under its synthetic id."""

    parsed = parsed_message(
        ChatMessage(
            chat_jid="4917000002@s.whatsapp.net",
            fallback_id="ios:41",
            sender_jid="4917000001@s.whatsapp.net",
            from_me=True,
            text="Old message",
        )
    )
    assert parsed.external_id == "4917000002@s.whatsapp.net/ios:41"
    assert parsed.direction == "outbound"
    assert parsed.thread is not None and parsed.thread.modality == "direct"


def test_media_failure_lands_a_marker_never_drops() -> None:
    """A media item without bytes becomes a marker part beside the text body."""

    parsed = parsed_message(
        ChatMessage(
            chat_jid="4917000002@s.whatsapp.net",
            stanza_id="M1",
            text="caption",
            media=(
                MediaItem(mime="image/jpeg", name="IMG_1.jpg", content=b"\xff\xd8jpeg"),
                MediaItem(mime="video/mp4", name="VID_2.mp4", content=None),
            ),
        )
    )
    assert parsed.body is not None
    assert parsed.body.type == "multipart/mixed"
    text, image, marker = parsed.body.children
    assert text.text == "caption"
    assert image.type == "image/jpeg" and image.disposition == "inline" and image.content
    assert marker.type == "text/plain" and "media unavailable: VID_2.mp4" in marker.text


def test_lone_document_media_is_an_attachment_part() -> None:
    """A single non-inline media message maps to a bare attachment part."""

    parsed = parsed_message(
        ChatMessage(
            chat_jid="4917000002@s.whatsapp.net",
            stanza_id="D1",
            media=(MediaItem(mime="application/pdf", name="contract.pdf", content=b"%PDF"),),
        )
    )
    assert parsed.body is not None
    assert parsed.body.type == "application/pdf"
    assert parsed.body.disposition == "attachment"
    assert parsed.body.name == "contract.pdf"


def test_reconciler_tick_owns_both_the_schedule_and_the_expiry() -> None:
    """The 'expires of one tick' invariant holds because both derive from one owner."""

    from angee.messaging_integrate_whatsapp.autoconfig import SETTINGS
    from angee.messaging_integrate_whatsapp.backend import SESSION_START_EXPIRES
    from angee.messaging_integrate_whatsapp.constants import ENSURE_SESSIONS_TASK, RECONCILER_INTERVAL

    assert SESSION_START_EXPIRES == RECONCILER_INTERVAL
    beat = SETTINGS["CELERY_BEAT_SCHEDULE:append"][ENSURE_SESSIONS_TASK]
    assert beat["task"] == ENSURE_SESSIONS_TASK
    assert beat["schedule"] == RECONCILER_INTERVAL


# --- (b) the live session against a fake vendor client ---


import threading  # noqa: E402
import time  # noqa: E402
from typing import Any, ClassVar  # noqa: E402

import pytest  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from rebac import system_context  # noqa: E402

from angee.integrate.locks import bridge_advisory_lock  # noqa: E402
from angee.integrate.sync import BridgeProgressReporter  # noqa: E402
from angee.messaging_integrate_whatsapp import session as session_module  # noqa: E402
from angee.messaging_integrate_whatsapp import tasks as tasks_module  # noqa: E402
from angee.messaging_integrate_whatsapp.backend import (  # noqa: E402
    RUN_SESSION_TASK,
    SESSION_QUEUE,
)
from angee.messaging_integrate_whatsapp.client import PairingState, SessionLoggedOut  # noqa: E402
from angee.messaging_integrate_whatsapp.session import WhatsAppSession  # noqa: E402
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration  # noqa: E402
from tests.test_messaging import MESSAGING_TEST_MODELS, Message, Thread  # noqa: E402
from tests.test_messaging_graphql import Channel  # noqa: E402

WHATSAPP_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)


@pytest.fixture
def whatsapp_tables(settings: Any, tmp_path: Any) -> Any:
    """Create the concrete messaging tables plus the Channel child.

    Also points ``ANGEE_DATA_DIR`` (a composed default absent from the bare
    test settings) at the test's tmp dir, so session stores never touch the
    repository tree.
    """

    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    created_models = _create_missing_tables(WHATSAPP_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(WHATSAPP_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _whatsapp_channel() -> Any:
    """Create a live-desired WhatsApp Channel row."""

    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    with system_context(reason="test whatsapp channel setup"):
        channel.subscription_state["desired"] = Channel.LiveState.LIVE
        channel.save(update_fields=["subscription_state", "updated_at"])
    return channel


class _Namespace:
    """Attribute bag standing in for a wire proto (absent fields read empty)."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    def __getattr__(self, name: str) -> str:
        return ""


def _jid(user: str, server: str = "s.whatsapp.net") -> _Namespace:
    return _Namespace(User=user, Server=server)


def _message_event(*, stanza: str, chat_user: str, sender_user: str, text: str) -> _Namespace:
    return _Namespace(
        Info=_Namespace(
            ID=stanza,
            Pushname="Ada",
            Timestamp=1_780_000_000,
            MessageSource=_Namespace(
                Chat=_jid(chat_user),
                Sender=_jid(sender_user),
                IsFromMe=False,
            ),
        ),
        Message=_Namespace(conversation=text),
    )


class _FakeRegistry:
    """Mirror of the vendor client's ``event`` attribute: callable + ``.qr``."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.qr_handler: Any = None

    def __call__(self, event_type: Any) -> Any:
        def register(handler: Any) -> Any:
            self.handlers[event_type.__name__] = handler
            return handler

        return register

    def qr(self, handler: Any) -> None:
        self.qr_handler = handler


class FakeWhatsAppClient:
    """Scripted vendor client: replays actions on connect, then blocks until stop."""

    script: ClassVar[tuple[Any, ...]] = ()
    media: ClassVar[bytes | None] = None
    instances: ClassVar[list["FakeWhatsAppClient"]] = []

    def __init__(self, store: str) -> None:
        self.store = store
        self.event = _FakeRegistry()
        self.stopped = threading.Event()
        type(self).instances.append(self)

    def connect(self) -> None:
        for action in type(self).script:
            action(self)
        self.stopped.wait(timeout=30)

    def stop(self) -> None:
        self.stopped.set()

    def download_any(self, payload: Any) -> bytes:
        if type(self).media is None:
            raise OSError("media expired")
        return type(self).media


def _run_session(channel: Any, *, script: tuple[Any, ...], stop_event: threading.Event | None = None) -> str:
    """Run one session against the fake client under a live reporter."""

    FakeWhatsAppClient.script = script
    FakeWhatsAppClient.instances = []
    session = WhatsAppSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=stop_event or threading.Event(),
    )
    session.client_class = FakeWhatsAppClient
    with system_context(reason="test whatsapp session run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        return session.run()


def _await(predicate: Any, *, timeout: float = 10.0) -> None:
    """Read-poll until ``predicate()`` is true — the script thread never writes.

    SQLite takes one writer at a time, so scripted actions synchronize on
    read-only row polls (tolerating a transient lock) and end the session via
    its stop event instead of writing the desired-state cross-thread.
    """

    from django.db.utils import OperationalError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except OperationalError:
            pass
        time.sleep(0.02)
    raise AssertionError("scripted condition never became true")


@pytest.mark.django_db(transaction=True)
def test_session_pairs_ingests_and_stops_cooperatively(whatsapp_tables: Any) -> None:
    """QR → paired → live message → cooperative stop, all over one session.

    The QR report lands as a data URI, pairing clears it and records the own
    JID, the message ingests through the shared write path into the chat's
    thread, and flipping the persisted desire ends the run within one wake.
    """

    channel = _whatsapp_channel()
    stop_event = threading.Event()
    seen: list[dict[str, Any]] = []

    def _pairing_state() -> Any:
        return (
            dict(Channel._base_manager.get(pk=channel.pk).sync_progress)
            .get("details", {})
            .get("pairing", {})
            .get("state")
        )

    def snapshot_when(state: str) -> Any:
        def action(_client: Any) -> None:
            _await(lambda: _pairing_state() == state)
            seen.append(dict(Channel._base_manager.get(pk=channel.pk).sync_progress))

        return action

    def finish(_client: Any) -> None:
        _await(lambda: Message._base_manager.count() == 1)
        stop_event.set()

    script = (
        lambda client: client.event.qr_handler(client, b"pairing-payload"),
        snapshot_when("awaiting_scan"),
        lambda client: client.event.handlers["PairStatus"](client, _Namespace(ID=_jid("4917000001"))),
        snapshot_when("paired"),
        lambda client: client.event.handlers["Message"](
            client,
            _message_event(stanza="3EB0AF", chat_user="4917000002", sender_user="4917000002", text="Hi!"),
        ),
        finish,
    )
    state = _run_session(channel, script=script, stop_event=stop_event)

    assert state == PairingState.PAIRED
    qr_report, paired_report = seen
    assert qr_report["details"]["pairing"]["state"] == "awaiting_scan"
    assert qr_report["details"]["pairing"]["qr"].startswith("data:image/png;base64,")
    assert paired_report["details"]["pairing"]["state"] == "paired"
    assert "qr" not in paired_report["details"]["pairing"]
    assert paired_report["details"]["pairing"]["phone"] == "+4917000001"

    channel.refresh_from_db()
    assert channel.subscription_state["own_jid"] == "4917000001@s.whatsapp.net"
    message = Message._base_manager.get()
    assert message.external_id == "4917000002@s.whatsapp.net/3EB0AF"
    thread = Thread._base_manager.get(pk=message.thread_id)
    assert thread.external_id == f"chat:{channel.pk}:4917000002@s.whatsapp.net"


@pytest.mark.django_db(transaction=True)
def test_session_wake_honors_persisted_stop_desire(whatsapp_tables: Any) -> None:
    """The bounded wake reads the base-owned desire — a stop ends the session."""

    channel = _whatsapp_channel()
    session = WhatsAppSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    with system_context(reason="test whatsapp desire check"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session._still_wanted() is True
        row = Channel._base_manager.get(pk=channel.pk)
        row.subscription_state["desired"] = Channel.LiveState.STOPPED
        row.save(update_fields=["subscription_state", "updated_at"])
        assert session._still_wanted() is False
        assert session.pairing == PairingState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_session_media_failure_lands_marker_not_loss(whatsapp_tables: Any) -> None:
    """An expired media download degrades to the marker part; the row still lands."""

    channel = _whatsapp_channel()
    stop_event = threading.Event()
    FakeWhatsAppClient.media = None
    media_event = _message_event(stanza="IMG01", chat_user="4917000002", sender_user="4917000002", text="")
    media_event.Message = _Namespace(
        imageMessage=_Namespace(mimetype="image/jpeg", fileName="IMG_1.jpg", caption="look")
    )

    def finish(_client: Any) -> None:
        _await(lambda: Message._base_manager.count() == 1)
        stop_event.set()

    script = (
        lambda client: client.event.handlers["Message"](client, media_event),
        finish,
    )
    _run_session(channel, script=script, stop_event=stop_event)

    message = Message._base_manager.get()
    with system_context(reason="test whatsapp media assertions"):
        texts = [part.fragment.text for part in message.parts.all() if part.fragment_id]
    assert any("media unavailable: IMG_1.jpg" in text for text in texts)
    assert any("look" in text for text in texts)


@pytest.mark.django_db(transaction=True)
def test_session_worker_shutdown_exits_within_one_wake(whatsapp_tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm worker shutdown ends an idle session without waiting on the socket."""

    monkeypatch.setattr(session_module, "WAKE_SECONDS", 0.05)
    channel = _whatsapp_channel()
    stop_event = threading.Event()
    stop_event.set()
    state = _run_session(channel, script=(), stop_event=stop_event)
    assert state == PairingState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_session_lost_lock_exits_for_clean_restart(whatsapp_tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dropped advisory lock ends the session instead of racing a duplicate."""

    monkeypatch.setattr(session_module, "WAKE_SECONDS", 0.05)
    monkeypatch.setattr(session_module, "bridge_is_locked", lambda _bridge: False)
    channel = _whatsapp_channel()
    state = _run_session(channel, script=())
    assert state == PairingState.STARTING


@pytest.mark.django_db(transaction=True)
def test_session_logged_out_raises_for_explicit_reset(whatsapp_tables: Any) -> None:
    """A phone-side unlink surfaces as SessionLoggedOut — no silent re-pairing."""

    channel = _whatsapp_channel()
    script = (lambda client: client.event.handlers["LoggedOut"](client, _Namespace()),)
    with pytest.raises(SessionLoggedOut):
        _run_session(channel, script=script)


@pytest.mark.django_db(transaction=True)
def test_channel_live_lifecycle_persists_desire_and_dispatches(
    whatsapp_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Channel.start_live persists the base-owned desire, then the backend enqueues."""

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.messaging_integrate_whatsapp.backend.enqueue_task",
        lambda name, *, kwargs, queue=None, expires=None, **_: sent.append(
            {"name": name, "kwargs": kwargs, "queue": queue, "expires": expires}
        ),
    )
    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    with system_context(reason="test start live"):
        channel.start_live()
    channel.refresh_from_db()
    assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
    assert sent == [
        {"name": RUN_SESSION_TASK, "kwargs": {"channel_id": channel.pk}, "queue": SESSION_QUEUE, "expires": 60.0}
    ]
    with system_context(reason="test stop live"):
        channel.stop_live()
    channel.refresh_from_db()
    assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_run_session_task_gates_on_desire_and_kind(whatsapp_tables: Any) -> None:
    """The task exits without connecting for foreign or not-live-desired channels."""

    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    result = tasks_module.run_session(channel.pk)
    assert result["skipped"] and result["reason"] == "not-live-desired"

    manual = make_integration("manualchan", model=Channel, backend_class="manual")
    result = tasks_module.run_session(manual.pk)
    assert result["skipped"] and result["reason"] == "not-a-whatsapp-channel"


@pytest.mark.django_db(transaction=True)
def test_run_session_task_records_logged_out_and_stops_desire(
    whatsapp_tables: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A logged-out session lands FAILED + stopped desire; the reconciler goes quiet."""

    channel = _whatsapp_channel()
    monkeypatch.setattr(WhatsAppSession, "client_class", FakeWhatsAppClient)
    FakeWhatsAppClient.script = (lambda client: client.event.handlers["LoggedOut"](client, _Namespace()),)

    result = tasks_module.run_session(channel.pk)
    assert result == {"ok": False, "logged_out": True}
    channel.refresh_from_db()
    assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
    assert channel.sync_stage == Channel.SyncStage.FAILED
    assert channel.next_sync_at is None

    sent: list[str] = []
    monkeypatch.setattr(tasks_module, "enqueue_task", lambda name, **_: sent.append(name))
    tasks_module.ensure_sessions()
    assert sent == []


@pytest.mark.django_db(transaction=True)
def test_ensure_sessions_redispatches_live_desired(whatsapp_tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reconciler re-enqueues live-desired channels with a one-tick expiry."""

    channel = _whatsapp_channel()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks_module,
        "enqueue_task",
        lambda name, *, kwargs, queue=None, expires=None, **_: sent.append(
            {"name": name, "kwargs": kwargs, "queue": queue, "expires": expires}
        ),
    )
    result = tasks_module.ensure_sessions()
    assert result["dispatched"] == 1
    assert sent == [
        {"name": RUN_SESSION_TASK, "kwargs": {"channel_id": channel.pk}, "queue": SESSION_QUEUE, "expires": 60.0}
    ]


# --- (c) the backup importer over synthesized ChatStorage fixtures ---


import plistlib  # noqa: E402
import sqlite3  # noqa: E402
from datetime import timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from django.core.management import CommandError  # noqa: E402

from angee.messaging_integrate_whatsapp.backup import (  # noqa: E402
    CORE_DATA_EPOCH,
    WHATSAPP_DOMAIN,
    BackupError,
    BackupImporter,
    IosBackup,
    open_chat_storage,
)
from tests.test_messaging import Handle, _storage_drive  # noqa: E402

_OWN_JID = "4917000001@s.whatsapp.net"
_DM_DATE = 700000000.5  # Core Data seconds — the epoch-conversion pin


def _manifest_file_id(relative: str) -> str:
    import hashlib

    return hashlib.sha1(f"{WHATSAPP_DOMAIN}-{relative}".encode()).hexdigest()


def _build_backup(tmp_path: Path, *, encrypted: bool = False) -> Path:
    """Synthesize an iPhone backup with two WhatsApp chats and media blobs."""

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": encrypted}))

    manifest = sqlite3.connect(backup / "Manifest.db")
    manifest.execute("CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER)")

    def place(relative: str, content: bytes, *, in_manifest: bool = True) -> None:
        file_id = _manifest_file_id(relative)
        if in_manifest:
            manifest.execute(
                "INSERT INTO Files VALUES (?, ?, ?, 1)", (file_id, WHATSAPP_DOMAIN, relative)
            )
        blob = backup / file_id[:2] / file_id
        blob.parent.mkdir(exist_ok=True)
        blob.write_bytes(content)

    store_path = tmp_path / "ChatStorage.sqlite"
    store = sqlite3.connect(store_path)
    store.executescript(
        """
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT, ZCONTACTNAME TEXT);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMEDIALOCALPATH TEXT, ZTITLE TEXT);
        CREATE TABLE ZWAMESSAGE (
            Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER,
            ZMEDIAITEM INTEGER, ZSTANZAID TEXT, ZISFROMME INTEGER, ZMESSAGEDATE REAL,
            ZTEXT TEXT, ZMESSAGETYPE INTEGER, ZFROMJID TEXT
        );
        """
    )
    store.execute("INSERT INTO ZWACHATSESSION VALUES (1, '4917000002@s.whatsapp.net', 'Bob')")
    store.execute("INSERT INTO ZWACHATSESSION VALUES (2, '111-222@g.us', 'Friends')")
    store.execute("INSERT INTO ZWAGROUPMEMBER VALUES (1, '4917000003@s.whatsapp.net', 'Carol')")
    store.execute("INSERT INTO ZWAMEDIAITEM VALUES (1, 'Media/IMG_1.txt', NULL)")
    store.execute("INSERT INTO ZWAMEDIAITEM VALUES (2, 'Media/GONE.mp4', NULL)")
    rows = [
        # (pk, chat, member, media, stanza, from_me, date, text, type, from_jid)
        (1, 1, None, None, "ABC123", 0, _DM_DATE, "Hello from backup", 0, "4917000002@s.whatsapp.net"),
        (2, 1, None, None, "DEF456", 1, _DM_DATE + 60, "My reply", 0, None),
        (3, 1, None, None, None, 0, _DM_DATE + 120, "prehistoric", 0, "4917000002@s.whatsapp.net"),
        (4, 1, None, None, "SYS1", 0, _DM_DATE + 180, "left the group", 6, None),
        (5, 1, None, 1, "MED1", 0, _DM_DATE + 240, None, 1, "4917000002@s.whatsapp.net"),
        (6, 1, None, 2, "MED2", 0, _DM_DATE + 300, None, 2, "4917000002@s.whatsapp.net"),
        (7, 2, 1, None, "GRP1", 0, _DM_DATE + 360, "Group hi", 0, None),
    ]
    store.executemany("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    store.commit()
    store.close()

    place("ChatStorage.sqlite", store_path.read_bytes())
    place("Media/IMG_1.txt", b"attachment bytes")
    # GONE.mp4 is deliberately absent from manifest and disk — the marker case.
    manifest.commit()
    manifest.close()
    return backup


def _import_backup(channel: Any, backup: Path, **kwargs: Any) -> int:
    chat_storage = open_chat_storage(backup)
    try:
        importer = BackupImporter(channel, chat_storage, own_jid=_OWN_JID, **kwargs)
        return importer.run()
    finally:
        chat_storage.close()  # also closes the backup manifest connection


@pytest.mark.django_db(transaction=True)
def test_backup_import_lands_chats_threads_and_identities(whatsapp_tables: Any, tmp_path: Any) -> None:
    """One import lands both chats: threads per chat, senders, media, epoch times."""

    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    with system_context(reason="test whatsapp backup drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)

    total = _import_backup(channel, backup)

    assert total == 6  # the type-6 system row is skipped
    threads = {thread.external_id for thread in Thread._base_manager.all()}
    assert threads == {
        f"chat:{channel.pk}:4917000002@s.whatsapp.net",
        f"chat:{channel.pk}:111-222@g.us",
    }

    hello = Message._base_manager.get(parts__fragment__text="Hello from backup")
    assert hello.external_id == "4917000002@s.whatsapp.net/ABC123"
    assert hello.direction == "inbound"
    assert hello.sent_at == CORE_DATA_EPOCH + timedelta(seconds=_DM_DATE)

    reply = Message._base_manager.get(parts__fragment__text="My reply")
    assert reply.direction == "outbound"
    with system_context(reason="test whatsapp backup senders"):
        assert reply.sender.external_id == _OWN_JID
        group_hi = Message._base_manager.get(parts__fragment__text="Group hi")
        assert group_hi.sender.external_id == "4917000003@s.whatsapp.net"
        assert group_hi.sender.display_name == "Carol"
        assert Handle.objects.filter(platform="whatsapp").count() >= 3

    prehistoric = Message._base_manager.get(parts__fragment__text="prehistoric")
    assert prehistoric.external_id == "4917000002@s.whatsapp.net/ios:3"

    with system_context(reason="test whatsapp backup media"):
        media = Message._base_manager.filter(parts__file__isnull=False).distinct().get()
        assert media.external_id == "4917000002@s.whatsapp.net/MED1"
        marker = Message._base_manager.filter(parts__fragment__text__contains="media unavailable").get()
        assert marker.external_id == "4917000002@s.whatsapp.net/MED2"


@pytest.mark.django_db(transaction=True)
def test_backup_import_is_idempotent(whatsapp_tables: Any, tmp_path: Any) -> None:
    """A re-run (resume) converges on the same rows instead of duplicating."""

    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    with system_context(reason="test whatsapp backup drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)

    assert _import_backup(channel, backup) == 6
    first_count = Message._base_manager.count()
    assert _import_backup(channel, backup) == 6
    assert Message._base_manager.count() == first_count


@pytest.mark.django_db(transaction=True)
def test_backup_and_live_converge_on_the_same_row(whatsapp_tables: Any, tmp_path: Any) -> None:
    """THE regression: a live event for an imported stanza lands on the same row.

    The live path carries a device-qualified chat/sender JID; normalization plus
    the chat-scoped stanza key must converge it onto the imported message and
    the imported thread.
    """

    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    with system_context(reason="test whatsapp convergence drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)
    _import_backup(channel, backup)
    imported = Message._base_manager.get(parts__fragment__text="Hello from backup")

    live = parsed_message(
        ChatMessage(
            chat_jid="4917000002:14@s.whatsapp.net",  # device-qualified, as the wire sends it
            stanza_id="ABC123",
            sender_jid="4917000002:14@s.whatsapp.net",
            sender_name="Bob",
            text="Hello from backup",
        )
    )
    with system_context(reason="test whatsapp convergence live ingest"):
        landed = Message.objects.ingest(
            [live], channel=channel, message_kind=Message.MessageKind.CHAT, quote_edges=False
        )

    assert [row.pk for row in landed] == [imported.pk]
    assert landed[0].thread_id == imported.thread_id
    assert Message._base_manager.count() == 6


@pytest.mark.django_db(transaction=True)
def test_encrypted_backup_fails_loudly(whatsapp_tables: Any, tmp_path: Any) -> None:
    """An encrypted backup is rejected with the actionable message, never parsed."""

    backup = _build_backup(tmp_path, encrypted=True)
    with pytest.raises(BackupError, match="encrypted"):
        IosBackup(backup)


@pytest.mark.django_db(transaction=True)
def test_whatsapp_import_command_dry_run_counts(whatsapp_tables: Any, tmp_path: Any) -> None:
    """The thin command wires the importer; --dry-run parses without writing."""

    channel = make_integration("whatsapp", model=Channel, backend_class="whatsapp")
    backup = _build_backup(tmp_path)
    call_command("whatsapp_import", str(backup), "--channel", channel.sqid, "--own-jid", _OWN_JID, "--dry-run")
    assert Message._base_manager.count() == 0

    with pytest.raises(CommandError, match="No WhatsApp channel"):
        call_command("whatsapp_import", str(backup), "--channel", "int_missing")
