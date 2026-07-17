"""Tests for Telegram Desktop's full-account takeout extractor client."""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from telethon import types, utils

from angee.addons import addon_contract
from angee.messaging.backends import MediaItem
from angee.messaging.managers import _bounded_message_metadata, _parsed_sync_hash
from angee.messaging_integrate_telegram import extractor as extractor_module
from angee.messaging_integrate_telegram import identity
from angee.messaging_integrate_telegram.autoconfig import SETTINGS as TELEGRAM_SETTINGS
from angee.messaging_integrate_telegram.extractor import TelegramTakeoutExtractor


class _TakeoutArchiveFile:
    """Minimal storage.File-shaped object opening one Telegram takeout ZIP."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def open_stream(self) -> Any:
        """Open the stored takeout bytes for recognition or execution."""

        return self.path.open("rb")


class _RecordingMessageManager:
    """Synchronous ingest double retaining immutable call and identity snapshots."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.landed: dict[str, object] = {}

    def ingest(self, parsed_messages: list[Any], **kwargs: Any) -> list[Any]:
        """Record one batch and mimic channel-scoped idempotent landing."""

        messages = list(parsed_messages)
        self.calls.append({"messages": messages, **kwargs})
        return [
            self.landed.setdefault(message.external_id, object())
            for message in messages
        ]


def test_telegram_takeout_recognition_is_bounded_for_a_padded_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recognition proves chats.list without reading a trailing multi-GB body."""

    consumed: list[int] = []

    class RecordingReader(extractor_module.BoundedReader):
        def read(self, size: int = -1) -> bytes:
            value = super().read(size)
            consumed.append(len(value))
            return value

    monkeypatch.setattr(extractor_module, "BoundedReader", RecordingReader)
    padding_size = 2 * 1024 * 1024
    archive = _takeout_archive(tmp_path, trailing_padding="x" * padding_size)

    assert TelegramTakeoutExtractor().recognizes(_TakeoutArchiveFile(archive)) is True
    assert sum(consumed) < padding_size // 4


def test_telegram_takeout_recognition_allows_large_sections_before_chats(
    tmp_path: Path,
) -> None:
    """A real full export may place more than 1 MiB before chats and reorder about."""

    archive = _takeout_archive(
        tmp_path,
        leading_padding="x" * (2 * 1024 * 1024),
        chats_before_about=True,
    )

    assert TelegramTakeoutExtractor().recognizes(_TakeoutArchiveFile(archive)) is True


def test_telegram_takeout_recognition_rejects_near_misses(tmp_path: Path) -> None:
    """Missing, structurally foreign, and non-ZIP inputs are hard False results."""

    extractor = TelegramTakeoutExtractor()

    missing_result = tmp_path / "missing-result.zip"
    with zipfile.ZipFile(missing_result, "w") as archive:
        archive.writestr("takeout/notes.txt", "not a Telegram takeout")
    assert extractor.recognizes(_TakeoutArchiveFile(missing_result)) is False

    wrong_shape = tmp_path / "wrong-shape.zip"
    with zipfile.ZipFile(wrong_shape, "w") as archive:
        archive.writestr(
            "takeout/result.json",
            json.dumps({"about": "Telegram export", "chats": {"items": []}}),
        )
    assert extractor.recognizes(_TakeoutArchiveFile(wrong_shape)) is False

    noise = tmp_path / "noise.bin"
    noise.write_bytes(b"\x00not a zip archive\xff" * 64)
    assert extractor.recognizes(_TakeoutArchiveFile(noise)) is False


@pytest.mark.parametrize(
    ("chat_type", "peer_kind", "peer"),
    [
        ("bot_chat", "user", types.PeerUser(42)),
        ("personal_chat", "user", types.PeerUser(42)),
        ("saved_messages", "user", types.PeerUser(42)),
        ("verification_codes", "user", types.PeerUser(42)),
        ("private_group", "chat", types.PeerChat(42)),
        ("private_supergroup", "channel", types.PeerChannel(42)),
        ("public_supergroup", "channel", types.PeerChannel(42)),
        ("private_channel", "channel", types.PeerChannel(42)),
        ("public_channel", "channel", types.PeerChannel(42)),
    ],
)
def test_export_chat_id_delegates_every_chat_type_to_telethon(
    chat_type: str,
    peer_kind: str,
    peer: Any,
) -> None:
    """Every Desktop chat token selects one Telethon-owned Peer marking path."""

    assert identity.export_peer_kind(chat_type) == peer_kind
    assert extractor_module.export_chat_id(chat_type, 42) == utils.get_peer_id(peer)


@pytest.mark.parametrize(
    ("chat_type", "chat_id"),
    [
        ("personal_chat", "not-numeric"),
        ("personal_chat", 0),
        ("personal_chat", -42),
        ("unknown_chat", 42),
    ],
)
def test_export_chat_id_rejects_unmarkable_export_identity(
    chat_type: str,
    chat_id: object,
) -> None:
    """Invalid bare ids and unknown vocabularies fail before neutral adaptation."""

    with pytest.raises(ValueError):
        extractor_module.export_chat_id(chat_type, chat_id)


def test_telegram_takeout_and_live_paths_converge_on_sync_identity(
    tmp_path: Path,
) -> None:
    """Takeout and live parsing produce identical external and sync-hash inputs."""

    export_root = tmp_path / "takeout"
    photo = export_root / "chats/chat_001/photos/photo.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"telegram-photo-bytes")
    marked_chat_id = utils.get_peer_id(types.PeerChannel(42))
    media = (MediaItem(mime="image/jpeg", content=photo.read_bytes()),)

    export_adapter = identity.parsed_export_message(
        _chat(chat_type="public_channel"),
        _message(),
        marked_chat_id=marked_chat_id,
        own_id="9999",
        media=media,
    )
    exported = extractor_module._parse_export_message(
        _chat(chat_type="public_channel"),
        _message(),
        own_id="9999",
        export_root=export_root,
    )
    live_adapter = identity.parsed_message(
        SimpleNamespace(
            id=17,
            raw_text="Hello from Telegram",
            message="Hello from Telegram",
            out=False,
            date=datetime.fromtimestamp(1784190600, tz=timezone.utc),
            reply_to_msg_id=None,
            media=object(),
            file=SimpleNamespace(mime_type="image/jpeg", name=None),
        ),
        chat_id=marked_chat_id,
        sender_id=4321,
        sender=SimpleNamespace(id=4321, first_name="Ada", last_name="Lovelace"),
        chat=SimpleNamespace(id=42, title="Angee News"),
        is_private=False,
        is_group=False,
        is_channel=True,
    )
    live_metadata = dict(live_adapter.metadata)
    live_facts = live_metadata.pop("_media_facts")
    live = replace(live_adapter, metadata=live_metadata).with_media(
        tuple(replace(fact, content=photo.read_bytes()) for fact in live_facts)
    )

    assert export_adapter.metadata["_media_facts"] == live_adapter.metadata["_media_facts"]
    assert exported.external_id == live.external_id == f"{marked_chat_id}/17"
    assert exported.thread is not None and live.thread is not None
    assert exported.thread.external_id == live.thread.external_id == str(marked_chat_id)
    assert _sync_hash(exported) == _sync_hash(live)


def test_telegram_takeout_broadcast_channel_uses_public_thread_modality() -> None:
    """The export adapter delegates broadcast modality to the live identity owner."""

    parsed = identity.parsed_export_message(
        _chat(chat_type="private_channel"),
        _message(),
        marked_chat_id=utils.get_peer_id(types.PeerChannel(42)),
    )

    assert parsed.thread is not None
    assert parsed.thread.modality == "public_thread"


@pytest.mark.django_db
def test_telegram_takeout_execute_delegates_to_messaging_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execution resolves the confirmed channel and lands media through ParsedMessage."""

    channel, manager, filters = _install_messaging_doubles(
        monkeypatch,
        subscription_state={"own_id": "9999"},
    )
    heartbeats: list[bool] = []
    reporter = SimpleNamespace(heartbeat=lambda: heartbeats.append(True))
    archive = _takeout_archive(tmp_path, include_media=True)

    result = TelegramTakeoutExtractor().execute(
        _TakeoutArchiveFile(archive),
        str(channel.sqid),
        reporter,
    )

    assert filters == [{"sqid": channel.sqid, "backend_class": "telegram"}]
    assert len(manager.calls) == 1
    assert manager.calls[0]["channel"] is channel
    assert manager.calls[0]["message_kind"] == "chat"
    assert manager.calls[0]["quote_edges"] is False
    parsed = manager.calls[0]["messages"][0]
    assert parsed.external_id == f"{utils.get_peer_id(types.PeerChannel(42))}/17"
    assert parsed.thread is not None and parsed.thread.modality == "public_thread"
    assert parsed.body is not None and parsed.body.type == "multipart/mixed"
    assert parsed.body.children[1].name == ""
    assert parsed.body.children[1].content == b"telegram-photo-bytes"
    assert heartbeats == [True]
    assert result == {
        "channel": channel.sqid,
        "imported": 1,
        "skipped": {"service": 0, "unknown_type": 0, "malformed": 0},
    }


@pytest.mark.django_db
def test_telegram_takeout_uses_export_owner_id_for_unpaired_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Personal information makes self-sent messages outbound before live pairing."""

    channel, manager, _filters = _install_messaging_doubles(
        monkeypatch,
        subscription_state={},
    )
    archive = _takeout_archive(tmp_path, own_id=4321)

    TelegramTakeoutExtractor().execute(
        _TakeoutArchiveFile(archive),
        str(channel.sqid),
        SimpleNamespace(heartbeat=lambda: None),
    )

    assert manager.calls[0]["messages"][0].direction == "outbound"


@pytest.mark.django_db
def test_telegram_takeout_reports_every_skipped_message_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service, unknown-chat, and malformed skips remain visible in the journal."""

    channel, manager, _filters = _install_messaging_doubles(monkeypatch)
    valid = _message(message_id=17)
    service = {**_message(message_id=18), "type": "service"}
    idless = {**_message(message_id=19), "id": ""}
    chats = [
        _chat(chat_type="public_channel", messages=[valid, service, idless]),
        _chat(chat_type="future_chat", messages=[_message(message_id=20)]),
        _chat(chat_type=None, chat_id=43, messages=[_message(message_id=21)]),
        _chat(chat_type="public_channel", chat_id="bad", messages=[_message(message_id=22)]),
    ]
    archive = _takeout_archive(tmp_path, chats=chats)

    result = TelegramTakeoutExtractor().execute(
        _TakeoutArchiveFile(archive),
        str(channel.sqid),
        SimpleNamespace(heartbeat=lambda: None),
    )

    assert len(manager.calls) == 1
    assert [message.external_id for message in manager.calls[0]["messages"]] == [
        f"{utils.get_peer_id(types.PeerChannel(42))}/17"
    ]
    assert result == {
        "channel": channel.sqid,
        "imported": 1,
        "skipped": {"service": 1, "unknown_type": 2, "malformed": 2},
    }


@pytest.mark.django_db
def test_telegram_takeout_rerun_converges_on_the_same_landed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-importing one takeout delegates the same external id to idempotent ingest."""

    channel, manager, _filters = _install_messaging_doubles(monkeypatch)
    archive = _takeout_archive(tmp_path)
    extractor = TelegramTakeoutExtractor()
    reporter = SimpleNamespace(heartbeat=lambda: None)

    first = extractor.execute(_TakeoutArchiveFile(archive), str(channel.sqid), reporter)
    second = extractor.execute(_TakeoutArchiveFile(archive), str(channel.sqid), reporter)

    assert first == second
    assert len(manager.calls) == 2
    assert manager.calls[0]["messages"][0].external_id == manager.calls[1]["messages"][0].external_id
    assert len(manager.landed) == 1


@pytest.mark.django_db
def test_telegram_takeout_flushes_multiple_bounded_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final partial batch follows a full size-triggered ingest batch."""

    monkeypatch.setattr(extractor_module, "_INGEST_BATCH_SIZE", 2)
    channel, manager, _filters = _install_messaging_doubles(monkeypatch)
    chats = [
        _chat(
            chat_type="public_channel",
            messages=[_message(message_id=value) for value in (1, 2, 3)],
        )
    ]
    archive = _takeout_archive(tmp_path, chats=chats)
    heartbeats: list[bool] = []

    result = TelegramTakeoutExtractor().execute(
        _TakeoutArchiveFile(archive),
        str(channel.sqid),
        SimpleNamespace(heartbeat=lambda: heartbeats.append(True)),
    )

    assert [len(call["messages"]) for call in manager.calls] == [2, 1]
    assert heartbeats == [True, True]
    assert result["imported"] == 3


def test_telegram_takeout_media_limits_and_bad_paths_degrade_to_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized or unsafe media remains visible without unbounded reads or aborts."""

    monkeypatch.setattr(extractor_module, "_MEDIA_READ_LIMIT", 4)
    export_root = tmp_path / "takeout"
    oversized = export_root / "media/video.mp4"
    oversized.parent.mkdir(parents=True)
    oversized.write_bytes(b"too-large")

    too_large = extractor_module._media_items(
        {
            "file": "media/video.mp4",
            "file_name": "video.mp4",
            "mime_type": "video/mp4",
        },
        export_root=export_root,
    )
    unsafe = extractor_module._media_items(
        {"photo": "../secret.jpg", "mime_type": "image/jpeg"},
        export_root=export_root,
    )

    assert too_large == (MediaItem(mime="video/mp4", name="video.mp4"),)
    assert unsafe == (MediaItem(mime="image/jpeg"),)
    marker = identity.parsed_export_message(
        _chat(chat_type="public_channel"),
        _message(),
        marked_chat_id=utils.get_peer_id(types.PeerChannel(42)),
    ).with_media(too_large)
    assert marker.body is not None
    assert "media unavailable: video.mp4" in str(marker.body)


def test_telegram_addon_registers_takeout_extractor_and_depends_on_bridge() -> None:
    """Telegram contributes one extractor through workflows-integrate autoconfig."""

    config = apps.get_app_config("messaging_integrate_telegram")
    contract = addon_contract(config)

    assert contract is not None
    assert "angee.workflows_integrate" in contract.depends_on
    assert TELEGRAM_SETTINGS[
        "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.telegram_takeout"
    ] == "angee.messaging_integrate_telegram.extractor.TelegramTakeoutExtractor"


def _sync_hash(message: Any) -> str:
    """Return messaging's exact sync hash for one neutral test message."""

    return _parsed_sync_hash(
        message,
        channel_id="channel-1",
        metadata=_bounded_message_metadata(message.metadata),
    )


def _install_messaging_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subscription_state: dict[str, Any] | None = None,
) -> tuple[Any, _RecordingMessageManager, list[dict[str, str]]]:
    """Install complete channel/message owner doubles for extractor execution."""

    channel = SimpleNamespace(
        sqid="int_confirmed",
        owner_id=99,
        subscription_state=subscription_state or {},
    )
    filters: list[dict[str, str]] = []

    class _ChannelQuery:
        def filter(self, **kwargs: str) -> _ChannelQuery:
            filters.append(kwargs)
            return self

        def first(self) -> Any:
            return channel

    manager = _RecordingMessageManager()
    models = {
        "Channel": SimpleNamespace(_base_manager=_ChannelQuery()),
        "Message": SimpleNamespace(
            objects=manager,
            MessageKind=SimpleNamespace(CHAT="chat"),
        ),
    }

    def get_model(app_label: str, model_name: str) -> Any:
        assert app_label == "messaging"
        return models[model_name]

    monkeypatch.setattr(extractor_module.apps, "get_model", get_model)
    return channel, manager, filters


def _chat(
    *,
    chat_type: str | None,
    chat_id: object = 42,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one Telegram Desktop chat with official bare peer identity."""

    chat: dict[str, Any] = {
        "id": chat_id,
        "name": "Angee News",
    }
    if chat_type is not None:
        chat["type"] = chat_type
    chat["messages"] = messages if messages is not None else [_message()]
    return chat


def _message(*, message_id: object = 17) -> dict[str, Any]:
    """Return one Telegram Desktop message with rich text and sibling media."""

    return {
        "id": message_id,
        "type": "message",
        "date": "2026-07-16T08:30:00+00:00",
        "date_unixtime": "1784190600",
        "from": "Ada Lovelace",
        "from_id": "user4321",
        "text": [
            {"type": "plain", "text": "Hello "},
            {"type": "bold", "text": "from Telegram"},
        ],
        "photo": "chats/chat_001/photos/photo.jpg",
        "mime_type": "image/jpeg",
    }


def _takeout_archive(
    tmp_path: Path,
    *,
    chats: list[dict[str, Any]] | None = None,
    own_id: object = 9999,
    include_media: bool = False,
    leading_padding: str = "",
    trailing_padding: str = "",
    chats_before_about: bool = False,
) -> Path:
    """Write a minimal full-account machine-readable Telegram Desktop export."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    chat_payload = {
        "about": "This list contains exported chats.",
        "list": chats or [_chat(chat_type="public_channel")],
    }
    payload: dict[str, Any] = {
        "personal_information": {"user": {"id": own_id}},
    }
    if leading_padding:
        payload["contacts"] = {"list": [leading_padding]}
    if chats_before_about:
        payload["chats"] = chat_payload
        payload["about"] = "Telegram Desktop data export"
    else:
        payload["about"] = "Telegram Desktop data export"
        payload["chats"] = chat_payload
    if trailing_padding:
        payload["padding"] = trailing_padding
    archive_path = tmp_path / "telegram-takeout.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("takeout/result.json", json.dumps(payload))
        if include_media:
            archive.writestr(
                "takeout/chats/chat_001/photos/photo.jpg",
                b"telegram-photo-bytes",
            )
    return archive_path
