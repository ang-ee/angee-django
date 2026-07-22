"""Tests for the iMessage/SMS channel addon.

Layered like the addon: (a) the pure identity/mapping rules in ``parser.py`` and
the ``attributedBody`` typedstream decoder, from literal shapes; (b) the backup
importer over ``sms.db`` fixtures synthesized in-test — direction from
``is_from_me``, phone/email identity, 2001 nanosecond timestamps, group vs direct,
media resolution, idempotency, and per-chat resume; (c) the mounted-drive
extractor over a reference backup. The neutral seam and the shared batching owner
are exercised through the same public ``import_backup`` path a real import uses.
"""

from __future__ import annotations

import hashlib
import plistlib
import sqlite3
import struct
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from rebac import system_context

from angee.integrate_iphone.backup import BackupError, IosBackup
from angee.messaging_integrate_imessage import mount_extractor
from angee.messaging_integrate_imessage.attributed_body import attributed_body_text
from angee.messaging_integrate_imessage.importer import import_backup
from angee.messaging_integrate_imessage.parser import (
    ChatMessage,
    external_id,
    handle_for_value,
    parsed_message,
)
from angee.messaging_integrate_imessage.store import (
    CORE_DATA_EPOCH,
    MEDIA_DOMAIN,
    SMS_DOMAIN,
    SMS_PATH,
    ImessageStore,
    has_sms_store,
)
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.test_messaging import MESSAGING_TEST_MODELS, Handle, Message, Thread, _storage_drive
from tests.test_messaging_graphql import Channel

UTC = timezone.utc

IMESSAGE_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)

_DM_SECONDS = 700_000_000.0  # Core Data seconds — the epoch-conversion pin (~2023)


# --- (a) parser identity/mapping rules and the attributedBody decoder ---


def test_handle_keeps_address_and_lowercases_identity() -> None:
    """The reachable address is the value; the identity folds case (emails)."""

    phone = handle_for_value("+15550001111")
    assert phone.platform == "imessage"
    assert phone.value == "+15550001111"
    assert phone.external_id == "+15550001111"

    email = handle_for_value("Bob@Example.COM", "Bob")
    assert email.value == "Bob@Example.COM"
    assert email.external_id == "bob@example.com"
    assert email.display_name == "Bob"


def test_external_id_is_the_global_guid_with_rowid_fallback() -> None:
    """Apple guids are globally unique, so the guid alone is the ingest key."""

    assert external_id("ABC-123") == "ABC-123"
    assert external_id("", "ios:42") == "ios:42"


def test_parsed_message_maps_inbound_direct() -> None:
    """An inbound direct message maps to a phone sender and a direct thread."""

    parsed = parsed_message(
        ChatMessage(
            chat_guid="iMessage;-;+15550001111",
            message_guid="GUID-1",
            chat_name="",
            group=False,
            sender_value="+15550001111",
            from_me=False,
            timestamp=CORE_DATA_EPOCH + timedelta(seconds=_DM_SECONDS),
            text="Hello",
            service="iMessage",
        )
    )
    assert parsed.external_id == "GUID-1"
    assert parsed.direction == "inbound"
    assert parsed.sender is not None and parsed.sender.external_id == "+15550001111"
    assert parsed.thread is not None
    assert parsed.thread.external_id == "iMessage;-;+15550001111"
    assert parsed.thread.modality == "direct"
    assert parsed.metadata == {"service": "iMessage", "chat_guid": "iMessage;-;+15550001111"}


def test_parsed_message_outbound_has_no_sender_and_group_thread() -> None:
    """An outbound row carries no sender; a group row maps to a group thread."""

    outbound = parsed_message(
        ChatMessage(chat_guid="c", message_guid="G", from_me=True, text="mine")
    )
    assert outbound.direction == "outbound"
    assert outbound.sender is None

    group = parsed_message(
        ChatMessage(chat_guid="g", message_guid="H", group=True, sender_value="+1", text="hi")
    )
    assert group.thread is not None and group.thread.modality == "group"


def test_attributed_body_decodes_short_and_long_forms() -> None:
    """The typedstream decoder reads both length-prefix forms; degrades to ''."""

    assert attributed_body_text(_attributed_body("Hello there")) == "Hello there"

    long_text = "x" * 400  # forces the 0x81 + uint16 count form
    assert attributed_body_text(_attributed_body(long_text)) == long_text

    assert attributed_body_text(None) == ""
    assert attributed_body_text(b"") == ""
    assert attributed_body_text(b"no marker here") == ""
    # A truncated count never raises — it degrades to empty text.
    assert attributed_body_text(b"streamtyped NSString \x2b\x81\x05") == ""


def test_attributed_body_decodes_uint32_form_and_survives_a_decoy_tag() -> None:
    """The uint32 count form decodes; a decoy class-chain tag degrades, never raises."""

    # Apple's 0x82 + uint32 count form (reserved for very large strings) decodes.
    assert attributed_body_text(_attributed_body("Big body", count_form="uint32")) == "Big body"

    # A stray 0x2b in the class chain before the real string tag must never raise;
    # it degrades to "" (or, were the count to line up, the correctly-located text).
    decoy = _attributed_body_with_decoy_tag("Real message")
    assert attributed_body_text(decoy) in ("", "Real message")


# --- (b) the backup importer over synthesized sms.db fixtures ---


@pytest.fixture
def imessage_tables(transactional_db: Any) -> Any:
    """Create the concrete messaging tables plus the Channel child."""

    del transactional_db
    created_models = _create_missing_tables(IMESSAGE_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(IMESSAGE_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def test_backup_import_lands_threads_identities_and_media(
    imessage_tables: Any, tmp_path: Any
) -> None:
    """One import lands every chat: threads, phone/email senders, media, epoch times."""

    channel = make_integration("imessage", model=Channel, backend_class="imessage")
    with system_context(reason="test imessage backup drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)

    total = import_backup(channel, backup)
    assert total == 7  # tapback + system rows are skipped

    threads = {thread.external_id for thread in Thread._base_manager.all()}
    assert threads == {
        f"chat:{channel.pk}:iMessage;-;+15550001111",
        f"chat:{channel.pk}:iMessage;+;chat9999",
        f"chat:{channel.pk}:iMessage;-;bob@example.com",
    }

    hello = Message._base_manager.get(parts__fragment__text="Hello from backup")
    assert hello.external_id == "GUID-1"
    assert hello.direction == "inbound"
    assert hello.sent_at == CORE_DATA_EPOCH + timedelta(seconds=_DM_SECONDS)

    reply = Message._base_manager.get(parts__fragment__text="My reply")
    assert reply.direction == "outbound"
    with system_context(reason="test imessage senders"):
        assert reply.sender is None
        assert hello.sender.external_id == "+15550001111"
        group_hi = Message._base_manager.get(parts__fragment__text="Group hi")
        assert group_hi.sender.external_id == "+15550002222"
        email_hi = Message._base_manager.get(parts__fragment__text="Email hi")
        assert email_hi.sender.external_id == "bob@example.com"
        assert Handle.objects.filter(platform="imessage").count() >= 3

    # attributedBody-only rows recover their text through the typedstream decoder.
    Message._base_manager.get(parts__fragment__text="From attributed body")

    with system_context(reason="test imessage media"):
        media = Message._base_manager.filter(parts__file__isnull=False).distinct().get()
        assert media.external_id == "GUID-MED1"
        marker = Message._base_manager.filter(parts__fragment__text__contains="media unavailable").get()
        assert marker.external_id == "GUID-MED2"


def test_backup_import_is_idempotent(imessage_tables: Any, tmp_path: Any) -> None:
    """A re-run converges on the same rows instead of duplicating."""

    channel = make_integration("imessage", model=Channel, backend_class="imessage")
    with system_context(reason="test imessage idempotent drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)

    assert import_backup(channel, backup) == 7
    first_count = Message._base_manager.count()
    assert import_backup(channel, backup) == 7
    assert Message._base_manager.count() == first_count


def test_backup_import_resume_advances_past_imported_prefix(
    imessage_tables: Any, tmp_path: Any
) -> None:
    """A resumed import skips each chat's imported prefix and advances to completion."""

    channel = make_integration("imessage", model=Channel, backend_class="imessage")
    with system_context(reason="test imessage resume drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)

    # A first pass interrupted after two messages, as the task soft-time-limit does.
    assert import_backup(channel, backup, limit=2) == 2
    assert Message._base_manager.count() == 2

    # Resume: the imported prefix sits below its chat's watermark and is skipped;
    # only the watermark row (idempotent) and newer rows re-flow, and the history
    # advances to complete rather than restarting.
    processed = import_backup(channel, backup, resume=True)
    assert processed == 6
    assert Message._base_manager.count() == 7


def test_encrypted_backup_fails_loudly(tmp_path: Any) -> None:
    """An encrypted backup is rejected with the actionable message, never parsed."""

    backup = _build_backup(tmp_path, encrypted=True)
    with pytest.raises(BackupError, match="encrypted"):
        IosBackup(backup)


def test_storeless_backup_reports_no_messages_store(tmp_path: Any) -> None:
    """A backup without sms.db is recognized as storeless and refuses to open."""

    backup = tmp_path / "empty"
    backup.mkdir()
    manifest = sqlite3.connect(backup / "Manifest.db")
    manifest.execute("CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER)")
    manifest.commit()
    manifest.close()

    probe = IosBackup(backup)
    assert has_sms_store(probe) is False
    probe.close()

    with pytest.raises(BackupError, match="no Messages store"):
        ImessageStore(IosBackup(backup))


def test_mount_backup_extractor_resolves_and_imports(
    imessage_tables: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mount extractor resolves a reference drive's backup root and imports it."""

    mount_root = tmp_path / "mnt"
    mount_root.mkdir()
    backup_dir = _build_backup(mount_root)  # -> mount_root/backup/Manifest.db + blobs
    manifest_path = backup_dir / "Manifest.db"
    fake_manifest = SimpleNamespace(
        storage=SimpleNamespace(path=lambda name: str(manifest_path)),
        storage_path="backup/Manifest.db",
    )
    monkeypatch.setattr(mount_extractor, "_manifest_file", lambda drive: fake_manifest)
    drive = object()

    channel = make_integration("imessage", model=Channel, backend_class="imessage")
    with system_context(reason="test imessage mount media drive"):
        _storage_drive(tmp_path / "media", owner=channel.owner)
    reporter = SimpleNamespace(heartbeat=lambda *args, **kwargs: None)

    assert mount_extractor.ImessageMountBackupExtractor().recognizes(drive) is True

    result = mount_extractor.ImessageMountBackupExtractor().execute(drive, channel.sqid, reporter)
    assert result == {"channel": str(channel.sqid), "imported": 7}
    assert Message._base_manager.count() == 7

    # Re-running resumes: each of the three chats re-flows only its watermark row.
    again = mount_extractor.ImessageMountBackupExtractor().execute(drive, channel.sqid, reporter)
    assert again["imported"] == 3
    assert Message._base_manager.count() == 7


def test_mount_extractor_rejects_a_storeless_drive(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recognition returns False when the drive exposes no on-disk backup."""

    monkeypatch.setattr(mount_extractor, "_manifest_file", lambda drive: None)
    assert mount_extractor.ImessageMountBackupExtractor().recognizes(object()) is False


def test_imessage_import_command_dry_run_counts(imessage_tables: Any, tmp_path: Any) -> None:
    """The thin command wires the importer; --dry-run parses without writing."""

    channel = make_integration("imessage", model=Channel, backend_class="imessage")
    with system_context(reason="test imessage command drive"):
        _storage_drive(tmp_path / "drive", owner=channel.owner)
    backup = _build_backup(tmp_path)

    call_command("imessage_import", str(backup), "--channel", channel.sqid, "--dry-run")
    assert Message._base_manager.count() == 0

    with pytest.raises(CommandError, match="No iMessage channel"):
        call_command("imessage_import", str(backup), "--channel", "int_missing")


# --- fixture builders ---


_TYPEDSTREAM_STRING_PREFIX = (
    b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@"
    b"\x84\x84\x12NSAttributedString\x00"
    b"\x84\x84\x08NSString\x01\x84\x84\x04NSObject\x00\x85\x2b"
)
"""An ``NSAttributedString`` typedstream header ending at the backing-string tag."""


def _attributed_body(text: str, *, count_form: str = "auto") -> bytes:
    """Build a minimal ``NSAttributedString`` typedstream carrying ``text``.

    Mirrors the layout :mod:`~.attributed_body` reads: an ``NSString`` class
    marker, the ``+`` (0x2b) bytes tag, Apple's variable-length count, then the
    UTF-8 payload. ``count_form`` selects the count encoding: ``"auto"`` uses the
    single byte when short and ``0x81`` + ``uint16`` otherwise; ``"uint32"`` forces
    the ``0x82`` + ``uint32`` form Apple reserves for very large strings.
    """

    payload = text.encode("utf-8")
    if count_form == "uint32":
        count = b"\x82" + struct.pack("<I", len(payload))
    elif len(payload) < 0x80:
        count = bytes([len(payload)])
    else:
        count = b"\x81" + struct.pack("<H", len(payload))
    return _TYPEDSTREAM_STRING_PREFIX + count + payload


def _attributed_body_with_decoy_tag(text: str) -> bytes:
    """Build a body whose class chain carries a stray ``0x2b`` before the real tag.

    A ``0x2b`` byte lands inside the ``NSObject`` class-chain region, ahead of the
    genuine backing-string tag, so the scanner meets the decoy first. The bytes
    following the decoy are not a valid count prefix, proving the decoder degrades
    to ``""`` on a misleading tag instead of raising.
    """

    payload = text.encode("utf-8")
    prefix = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@"
        b"\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSString\x01\x2b\x84\x84\x04NSObject\x00\x85\x2b"
    )
    return prefix + bytes([len(payload)]) + payload


def _manifest_file_id(relative: str, domain: str) -> str:
    return hashlib.sha1(f"{domain}-{relative}".encode()).hexdigest()


def _build_sms_store(path: Path) -> None:
    """Synthesize the ``sms.db`` schema subset with direct, group, and email chats."""

    store = sqlite3.connect(path)
    store.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
            display_name TEXT, style INTEGER, room_name TEXT
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
            handle_id INTEGER, service TEXT, date INTEGER, is_from_me INTEGER,
            associated_message_type INTEGER, item_type INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT, transfer_name TEXT
        );
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        """
    )
    store.execute("INSERT INTO handle VALUES (1, '+15550001111')")
    store.execute("INSERT INTO handle VALUES (2, '+15550002222')")
    store.execute("INSERT INTO handle VALUES (3, 'bob@example.com')")
    store.execute(
        "INSERT INTO chat VALUES (1, 'iMessage;-;+15550001111', '+15550001111', NULL, 45, NULL)"
    )
    store.execute(
        "INSERT INTO chat VALUES (2, 'iMessage;+;chat9999', 'chat9999', 'Friends', 43, 'chat9999')"
    )
    store.execute(
        "INSERT INTO chat VALUES (3, 'iMessage;-;bob@example.com', 'bob@example.com', NULL, 45, NULL)"
    )
    ns = int(_DM_SECONDS * 1e9)
    attributed = _attributed_body("From attributed body")
    messages = [
        # (rowid, guid, text, attributedBody, handle, service, date, from_me, assoc, item)
        (1, "GUID-1", "Hello from backup", None, 1, "iMessage", ns, 0, None, 0),
        (2, "GUID-2", "My reply", None, 0, "iMessage", ns + 60 * 10**9, 1, None, 0),
        (3, "GUID-TB", "Liked a message", None, 1, "iMessage", ns + 90 * 10**9, 0, 2000, 0),
        (4, "GUID-SYS", None, None, 1, "iMessage", ns + 120 * 10**9, 0, None, 1),
        (5, "GUID-AB", None, attributed, 1, "iMessage", ns + 180 * 10**9, 0, None, 0),
        (6, "GUID-MED1", None, None, 1, "iMessage", ns + 240 * 10**9, 0, None, 0),
        (7, "GUID-MED2", None, None, 1, "iMessage", ns + 300 * 10**9, 0, None, 0),
        (8, "GUID-GRP", "Group hi", None, 2, "iMessage", ns + 360 * 10**9, 0, None, 0),
        (9, "GUID-EMAIL", "Email hi", None, 3, "SMS", ns + 420 * 10**9, 0, None, 0),
    ]
    store.executemany("INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?,?)", messages)
    store.executemany(
        "INSERT INTO chat_message_join VALUES (?, ?)",
        [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (2, 8), (3, 9)],
    )
    store.execute(
        "INSERT INTO attachment VALUES "
        "(1, '~/Library/SMS/Attachments/00/00/G1/IMG_1.txt', 'text/plain', 'IMG_1.txt')"
    )
    store.execute(
        "INSERT INTO attachment VALUES "
        "(2, '~/Library/SMS/Attachments/11/11/G2/GONE.mp4', 'video/mp4', 'GONE.mp4')"
    )
    store.executemany("INSERT INTO message_attachment_join VALUES (?, ?)", [(6, 1), (7, 2)])
    store.commit()
    store.close()


def _build_backup(tmp_path: Path, *, encrypted: bool = False) -> Path:
    """Synthesize an iPhone backup carrying the Messages store and one attachment blob."""

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": encrypted}))

    manifest = sqlite3.connect(backup / "Manifest.db")
    manifest.execute("CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER)")

    def place(domain: str, relative: str, content: bytes) -> None:
        file_id = _manifest_file_id(relative, domain)
        manifest.execute("INSERT INTO Files VALUES (?, ?, ?, 1)", (file_id, domain, relative))
        blob = backup / file_id[:2] / file_id
        blob.parent.mkdir(exist_ok=True)
        blob.write_bytes(content)

    store_path = tmp_path / "sms.db"
    _build_sms_store(store_path)
    place(SMS_DOMAIN, SMS_PATH, store_path.read_bytes())
    place(MEDIA_DOMAIN, "Library/SMS/Attachments/00/00/G1/IMG_1.txt", b"attachment bytes")
    # GONE.mp4 is deliberately absent from manifest and disk — the marker case.
    manifest.commit()
    manifest.close()
    return backup
