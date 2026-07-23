"""Telegram Desktop takeout client for the archive workflow bridge.

Recognition structurally proves ``result.json``'s machine-readable top-level
``chats.list`` under explicit ZIP and member-read budgets, independent of key
order; it never loads the exported message body. Execution safely stages the containing
subtree under a declared-size cap, streams its chat messages, delegates identity
and modality to :mod:`angee.messaging_integrate_telegram.identity`, and batches
neutral messages through messaging's idempotent ingest owner.
"""

from __future__ import annotations

import codecs
import json
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, TextIO, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import system_context
from telethon import types, utils

from angee.messaging.backends import MediaItem, ParsedMessage
from angee.messaging_integrate_telegram.backend import TelegramChannelBackend
from angee.messaging_integrate_telegram.identity import (
    export_peer_kind,
    media_fact,
    parsed_export_message,
)
from angee.workflows_integrate.archives import (
    ArchiveError,
    BoundedReader,
    archive_entries,
    safe_member_name,
    stage_subtree,
)
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor

_ARCHIVE_RECOGNITION_READ_LIMIT = 16 * 1024 * 1024
"""Compressed bytes available to ZIP metadata and each ``result.json`` probe."""

_RESULT_RECOGNITION_READ_LIMIT = _ARCHIVE_RECOGNITION_READ_LIMIT
"""Uncompressed JSON bytes available to one structural recognition probe."""

_INGEST_BATCH_SIZE = 500
"""Maximum exported messages retained before one messaging ingest call."""

_INGEST_BATCH_BYTES = 64 * 1024 * 1024
"""Maximum resolved media bytes retained before one messaging ingest call."""

_MEDIA_READ_LIMIT = 64 * 1024 * 1024
"""Maximum bytes read into memory for one exported media item."""

_MEDIA_PATH_FIELDS = ("photo", "file")


class TelegramTakeoutExtractor(ArchiveExtractor):
    """Recognize and import a full-account Telegram Desktop JSON export.

    Recognition is deliberately a hard boolean and reads only enough JSON tokens
    to prove the expected top-level shape. A multi-gigabyte ``result.json`` is
    never materialized by the workflow probe; full message parsing happens only
    after the operator confirms a Telegram channel target. Version 1 accepts the
    machine-readable full-account export containing top-level ``chats.list``;
    per-chat JSON exports are intentionally outside this extractor's scope.
    """

    key = "telegram_takeout"
    label = "Telegram Desktop export"
    target_resource = "messaging.Channel"

    def recognizes(self, file: Any) -> bool:
        """Return whether a bounded ``result.json`` probe proves takeout shape."""

        try:
            with file.open_stream() as stream:
                bounded = BoundedReader(
                    stream,
                    limit=_ARCHIVE_RECOGNITION_READ_LIMIT,
                )
                with zipfile.ZipFile(cast(BinaryIO, bounded)) as archive:
                    return _recognized_result(archive, budget=bounded) is not None
        except (
            ArchiveError,
            json.JSONDecodeError,
            NotImplementedError,
            OSError,
            UnicodeError,
            ValueError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
        ):
            return False

    def execute(
        self,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Stream the staged takeout through the confirmed channel's ingest path."""

        channel = _confirmed_channel(target_pk)
        result = _import_archive(file, channel=channel, reporter=reporter)
        return {"channel": str(channel.sqid), **result}


def export_chat_id(chat_type: object, chat_id: object) -> int:
    """Ask Telethon to mark one Desktop bare id for its export-owned peer kind."""

    try:
        bare_id = int(str(chat_id).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"Telegram export chat id {chat_id!r} is not numeric.") from error
    if bare_id <= 0:
        raise ValueError(f"Telegram export chat id {chat_id!r} must be positive.")
    peer_kind = export_peer_kind(chat_type)
    if peer_kind == "user":
        peer: Any = types.PeerUser(bare_id)
    elif peer_kind == "chat":
        peer = types.PeerChat(bare_id)
    else:
        peer = types.PeerChannel(bare_id)
    return int(utils.get_peer_id(peer))


class _JsonReader:
    """Small incremental JSON reader for locating and iterating takeout arrays.

    The stdlib owns JSON scalar/object decoding. This cursor adds only the
    streaming mechanics the stdlib lacks: bounded chunk reads and structural
    skipping of unrelated top-level values without retaining their bodies.
    """

    def __init__(self, stream: TextIO, *, chunk_size: int = 64 * 1024) -> None:
        self.stream = stream
        self.chunk_size = chunk_size
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def peek(self) -> str:
        """Return the next non-whitespace character without consuming it."""

        self._skip_whitespace()
        self._ensure_character()
        return self.buffer[self.position]

    def expect(self, expected: str) -> None:
        """Consume one expected structural character or reject the document."""

        actual = self.take()
        if actual != expected:
            raise ArchiveError(
                f"Telegram result.json expected {expected!r}, found {actual!r}."
            )

    def take(self) -> str:
        """Consume and return the next non-whitespace character."""

        self._skip_whitespace()
        self._ensure_character()
        value = self.buffer[self.position]
        self.position += 1
        return value

    def value(self) -> Any:
        """Decode one JSON value, retaining only that value's buffered text."""

        self._skip_whitespace()
        self._compact(force=True)
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
            except json.JSONDecodeError as error:
                if self.eof:
                    raise ArchiveError("Telegram result.json contains an invalid value.") from error
                self._read_more(compact=False)
                continue
            self.position = end
            return value

    def skip_value(self) -> None:
        """Discard one JSON value structurally without materializing its body."""

        first = self.peek()
        if first == '"':
            self._skip_string()
            return
        if first in "[{":
            self._skip_container()
            return
        token: list[str] = []
        while True:
            try:
                character = self._character()
            except ArchiveError:
                break
            if character in " \t\r\n,]}":
                break
            token.append(character)
            self.position += 1
        try:
            json.loads("".join(token))
        except json.JSONDecodeError as error:
            raise ArchiveError("Telegram result.json contains an invalid scalar.") from error

    def _skip_container(self) -> None:
        """Discard one nested object or array with string-aware bracket matching."""

        opening = self.take()
        stack = ["}" if opening == "{" else "]"]
        in_string = False
        escaped = False
        while stack:
            character = self._character()
            self.position += 1
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                stack.append("}")
            elif character == "[":
                stack.append("]")
            elif character in "]}":
                if character != stack.pop():
                    raise ArchiveError("Telegram result.json has mismatched containers.")

    def _skip_string(self) -> None:
        """Discard one JSON string without retaining its contents."""

        self.expect('"')
        escaped = False
        while True:
            character = self._character()
            self.position += 1
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                return

    def _skip_whitespace(self) -> None:
        """Advance over JSON whitespace, reading another bounded chunk as needed."""

        while True:
            if self.position >= len(self.buffer):
                self._read_more()
                if self.position >= len(self.buffer):
                    return
            if not self.buffer[self.position].isspace():
                return
            self.position += 1

    def _character(self) -> str:
        """Return the next raw character without consuming it."""

        self._ensure_character()
        return self.buffer[self.position]

    def _ensure_character(self) -> None:
        """Ensure one character is available or raise a stable archive error."""

        while self.position >= len(self.buffer) and not self.eof:
            self._read_more()
        if self.position >= len(self.buffer):
            raise ArchiveError("Telegram result.json ended before its expected shape.")

    def _read_more(self, *, compact: bool = True) -> None:
        """Read one bounded text chunk, optionally preserving an incomplete value."""

        if self.eof:
            return
        if compact:
            self._compact()
        chunk = self.stream.read(self.chunk_size)
        if chunk:
            self.buffer += chunk
        else:
            self.eof = True

    def _compact(self, *, force: bool = False) -> None:
        """Release consumed text while preserving the unread suffix."""

        if self.position and (force or self.position >= self.chunk_size):
            self.buffer = self.buffer[self.position :]
            self.position = 0


def _recognized_result(
    archive: zipfile.ZipFile,
    *,
    budget: BoundedReader | None = None,
) -> str | None:
    """Return the first bounded ``result.json`` proving Telegram takeout shape."""

    entries = archive_entries(archive)
    candidates = sorted(
        name for name in entries if PurePosixPath(name).name == "result.json"
    )
    for name in candidates:
        if budget is not None:
            budget.remaining = _ARCHIVE_RECOGNITION_READ_LIMIT
        try:
            with archive.open(entries[name]) as stream:
                bounded = BoundedReader(
                    cast(BinaryIO, stream),
                    limit=_RESULT_RECOGNITION_READ_LIMIT,
                )
                text_stream = codecs.getreader("utf-8")(bounded)
                _position_at_chats_list(
                    _JsonReader(cast(TextIO, text_stream), chunk_size=4096)
                )
            return name
        except (ArchiveError, json.JSONDecodeError, UnicodeError, ValueError):
            continue
    return None


def _position_at_chats_list(reader: _JsonReader) -> object:
    """Position after top-level ``chats.list`` and return preceding own user id."""

    reader.expect("{")
    own_id: object = ""
    first = True
    while reader.peek() != "}":
        if not first:
            reader.expect(",")
        first = False
        key = reader.value()
        if not isinstance(key, str):
            raise ArchiveError("Telegram result.json object keys must be strings.")
        reader.expect(":")
        if key == "personal_information":
            own_id = _personal_information_own_id(reader.value())
            continue
        if key == "chats":
            _position_at_named_list(reader, "list")
            return own_id
        reader.skip_value()
    raise ArchiveError("Telegram result.json has no chats object.")


def _personal_information_own_id(value: object) -> object:
    """Return ``personal_information.user.id`` without interpreting its identity."""

    if not isinstance(value, Mapping):
        return ""
    user = value.get("user")
    return user.get("id", "") if isinstance(user, Mapping) else ""


def _position_at_named_list(reader: _JsonReader, name: str) -> None:
    """Position ``reader`` just after array ``name`` in the current object."""

    reader.expect("{")
    first = True
    while reader.peek() != "}":
        if not first:
            reader.expect(",")
        first = False
        key = reader.value()
        if not isinstance(key, str):
            raise ArchiveError("Telegram result.json object keys must be strings.")
        reader.expect(":")
        if key == name:
            reader.expect("[")
            return
        reader.skip_value()
    raise ArchiveError(f"Telegram result.json object has no {name!r} list.")


@contextmanager
def _export_messages(
    result_path: Path,
) -> Iterator[
    tuple[
        object,
        Iterator[tuple[dict[str, Any], dict[str, Any] | None]],
    ]
]:
    """Open one export parse, yielding its own id and streamed chat messages."""

    with result_path.open("r", encoding="utf-8") as stream:
        reader = _JsonReader(stream)
        own_id = _position_at_chats_list(reader)
        yield own_id, _iter_export_chats(reader)


def _iter_export_chats(
    reader: _JsonReader,
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Yield every chat/message pair from the positioned ``chats.list`` array."""

    first = True
    while reader.peek() != "]":
        if not first:
            reader.expect(",")
        first = False
        yield from _iter_chat_messages(reader)
    reader.expect("]")


def _iter_chat_messages(
    reader: _JsonReader,
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Yield messages from one Desktop chat whose metadata precedes its list."""

    reader.expect("{")
    chat: dict[str, Any] = {}
    first = True
    while reader.peek() != "}":
        if not first:
            reader.expect(",")
        first = False
        key = reader.value()
        if not isinstance(key, str):
            raise ArchiveError("Telegram chat object keys must be strings.")
        reader.expect(":")
        if key in {"id", "name", "type"}:
            chat[key] = reader.value()
            continue
        if key != "messages":
            reader.skip_value()
            continue
        reader.expect("[")
        message_first = True
        while reader.peek() != "]":
            if not message_first:
                reader.expect(",")
            message_first = False
            value = reader.value()
            yield chat, dict(value) if isinstance(value, Mapping) else None
        reader.expect("]")
    reader.expect("}")


def _import_archive(
    file: Any,
    *,
    channel: Any,
    reporter: ArchiveExecutionReporter,
) -> dict[str, Any]:
    """Safely stage one takeout subtree and stream it into messaging ingest."""

    try:
        with file.open_stream() as stream, zipfile.ZipFile(stream) as archive:
            result_name = _recognized_result(archive)
            if result_name is None:
                raise ArchiveError(
                    "This archive contains no machine-readable Telegram Desktop export."
                )
            parent = PurePosixPath(result_name).parent
            with stage_subtree(archive, parent) as export_root:
                result_path = export_root / PurePosixPath(result_name).name
                return _import_result(
                    result_path,
                    export_root=export_root,
                    channel=channel,
                    reporter=reporter,
                )
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ArchiveError("This file is not a readable ZIP export archive.") from error


def _import_result(
    result_path: Path,
    *,
    export_root: Path,
    channel: Any,
    reporter: ArchiveExecutionReporter,
) -> dict[str, Any]:
    """Batch streamed messages and report every recoverable skip category."""

    message_model = apps.get_model("messaging", "Message")
    fallback_own_id = channel.subscription_state.get("own_id", "")
    batch: list[ParsedMessage] = []
    batch_bytes = 0
    total = 0
    skipped = {"service": 0, "unknown_type": 0, "malformed": 0}

    def flush() -> None:
        nonlocal batch_bytes, total
        if not batch:
            return
        with system_context(reason="messaging_integrate_telegram.takeout_import"):
            message_model.objects.ingest(
                batch,
                channel=channel,
                quote_edges=False,
            )
        total += len(batch)
        batch.clear()
        batch_bytes = 0
        reporter.heartbeat()

    with _export_messages(result_path) as (export_own_id, messages):
        own_id = export_own_id or fallback_own_id
        for chat, message in messages:
            if message is None:
                skipped["malformed"] += 1
                continue
            if str(message.get("type") or "") != "message":
                skipped["service"] += 1
                continue
            if not message.get("id"):
                skipped["malformed"] += 1
                continue
            try:
                export_peer_kind(chat.get("type"))
            except ValueError:
                skipped["unknown_type"] += 1
                continue
            try:
                parsed = _parse_export_message(
                    chat,
                    message,
                    own_id=own_id,
                    export_root=export_root,
                )
            except ValueError:
                skipped["malformed"] += 1
                continue
            batch.append(parsed)
            batch_bytes += _body_content_bytes(parsed.body)
            if len(batch) >= _INGEST_BATCH_SIZE or batch_bytes >= _INGEST_BATCH_BYTES:
                flush()
    flush()
    return {"imported": total, "skipped": skipped}


def _parse_export_message(
    chat: Mapping[str, Any],
    message: Mapping[str, Any],
    *,
    own_id: object,
    export_root: Path,
) -> ParsedMessage:
    """Mark identity through Telethon, then resolve neutral export media."""

    marked_chat_id = export_chat_id(chat.get("type"), chat.get("id"))
    media = _media_items(message, export_root=export_root)
    parsed = parsed_export_message(
        chat,
        message,
        marked_chat_id=marked_chat_id,
        own_id=own_id,
        media=media,
    )
    if not media:
        return parsed
    metadata = dict(parsed.metadata)
    metadata.pop("_media_facts", None)
    return replace(parsed, metadata=metadata).with_media(media)


def _body_content_bytes(part: Any | None) -> int:
    """Return resolved content bytes retained by one neutral body tree."""

    if part is None:
        return 0
    own = len(part.content) if part.content is not None else 0
    return own + sum(_body_content_bytes(child) for child in part.children)


def _media_items(
    message: Mapping[str, Any],
    *,
    export_root: Path,
) -> tuple[MediaItem, ...]:
    """Resolve bounded media bytes; unsafe, missing, or large files stay visible."""

    items: list[MediaItem] = []
    for field in _MEDIA_PATH_FIELDS:
        raw_path = message.get(field)
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        clean_path = raw_path.strip()
        fact = media_fact(
            message.get("mime_type") or ("image/jpeg" if field == "photo" else ""),
            message.get("file_name"),
        )
        content: bytes | None = None
        try:
            relative = safe_member_name(clean_path)
        except ArchiveError:
            pass
        else:
            path = export_root.joinpath(*PurePosixPath(relative).parts)
            try:
                if path.is_file() and path.stat().st_size <= _MEDIA_READ_LIMIT:
                    content = path.read_bytes()
            except OSError:
                content = None
        items.append(
            MediaItem(
                mime=fact.mime,
                name=fact.name,
                content=content,
            )
        )
    return tuple(items)


def _confirmed_channel(target_pk: str) -> Any:
    """Return the confirmed Telegram channel named by its public sqid."""

    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_telegram.archive_import.channel"):
        channel = channel_model._base_manager.filter(
            sqid=target_pk,
            backend_class=TelegramChannelBackend.key,
        ).first()
    if channel is None:
        raise ValidationError({"target": f"No Telegram channel {target_pk!r}."})
    return channel
