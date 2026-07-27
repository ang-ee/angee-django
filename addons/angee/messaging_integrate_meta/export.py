"""Meta Download Your Information discovery and neutral message decoding.

Meta emits Facebook and Instagram archives with the same dialect: recursively
mojibaked JSON strings, millisecond message timestamps, numbered message chunks,
and attachment URIs rooted at one of several overlapping export trees. This
module owns that format once. Network addons provide the activity-directory
name, folder inventory, platform, and identity-confidence annotation.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from angee.messaging.backends import (
    MediaItem,
    ParsedHandle,
    ParsedMessage,
    ParsedRecipient,
    ParsedThread,
    body_part,
)
from angee.messaging.backup_ingest import ContentKeyCounter

_MESSAGE_FILE = re.compile(r"^message_(\d+)\.json$")
_MEDIA_KEYS = ("photos", "videos", "audio_files", "files", "gifs")


def message_content_projection(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the author-written identity content of one Meta message record.

    Derived external ids digest this projection, never the whole record:
    engagement (``reactions``) accrues on old messages and Meta's export
    revisions add cosmetic envelope keys, and neither may shift a record's
    identity — an id shift duplicates the row on the next re-export import.
    Media identifies by attachment basename (the fbid-named file), which
    survives the thread-folder move that rewrites the full ``uri``.
    """

    projection: dict[str, Any] = {"content": str(data.get("content") or "")}
    for key in ("share", "sticker"):
        if data.get(key) is not None:
            projection[key] = data[key]
    media: list[str] = []
    for key in _MEDIA_KEYS:
        raw_items = data.get(key)
        if not isinstance(raw_items, list):
            continue
        media.extend(
            PurePosixPath(str(item.get("uri") or "")).name
            for item in raw_items
            if isinstance(item, Mapping)
        )
    if media:
        projection["media"] = media
    return projection


def fix_encoding(value: Any) -> Any:
    """Recursively reverse Meta's UTF-8-bytes-as-Latin-1 mojibake.

    Strings that are already valid Unicode, or cannot make the round trip, are
    returned unchanged. Lists, tuples, and mapping keys/values retain their
    container shape while every nested string is normalized.
    """

    if isinstance(value, str):
        try:
            return value.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return value
    if isinstance(value, list):
        return [fix_encoding(item) for item in value]
    if isinstance(value, tuple):
        return tuple(fix_encoding(item) for item in value)
    if isinstance(value, dict):
        return {fix_encoding(key): fix_encoding(item) for key, item in value.items()}
    return value


def load_json(path: Path | str) -> Any:
    """Load one Meta JSON file and normalize every mojibaked string."""

    with Path(path).open(encoding="utf-8") as stream:
        return fix_encoding(json.load(stream))


def timestamp_milliseconds(value: Any) -> datetime | None:
    """Convert Meta's message millisecond timestamp to aware UTC."""

    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    if not milliseconds:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def timestamp_seconds(value: Any) -> datetime | None:
    """Convert Meta's non-message second timestamp to aware UTC."""

    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


@dataclass(frozen=True)
class MetaMessageRecord:
    """One id-assigned message from a merged Meta thread export."""

    data: Mapping[str, Any]
    external_id: str
    thread_key: str
    thread_title: str
    thread_metadata: dict[str, Any]
    participants: tuple[str, ...]
    sent_at: datetime
    media: tuple[MediaItem, ...] = ()


class MetaExport:
    """Read-only view across every Facebook/Instagram export root below a path."""

    def __init__(
        self,
        root: Path | str,
        *,
        activity_directory: str,
        root_prefix: str,
    ) -> None:
        self.root = Path(root)
        self.activity_directory = activity_directory
        self.root_prefix = root_prefix
        self.roots = self._discover_roots()
        if not self.roots:
            raise ValueError(
                f"No {activity_directory!r} Meta export tree found below {self.root}."
            )

    def _discover_roots(self) -> tuple[Path, ...]:
        """Return deterministic named roots plus the root-level bare activity tree."""

        candidates: list[Path] = []
        if (self.root / self.activity_directory).is_dir():
            candidates.append(self.root)
        try:
            children = sorted(self.root.iterdir(), key=lambda path: path.name)
        except OSError:
            children = []
        candidates.extend(
            child
            for child in children
            if child.is_dir()
            and child.name.startswith(self.root_prefix)
            and (child / self.activity_directory).is_dir()
        )
        return tuple(dict.fromkeys(candidates))

    def paths(self, *parts: str) -> tuple[Path, ...]:
        """Return every existing occurrence of one relative export path."""

        return tuple(path for root in self.roots if (path := root.joinpath(*parts)).exists())

    def first_path(self, *parts: str) -> Path | None:
        """Return the first deterministic occurrence of one relative path."""

        return next(iter(self.paths(*parts)), None)

    def glob(self, *parents: str, pattern: str) -> tuple[Path, ...]:
        """Return all matching files across roots in deterministic path order."""

        paths = {
            path
            for root in self.roots
            for path in root.joinpath(*parents).glob(pattern)
            if path.is_file()
        }
        return tuple(sorted(paths, key=lambda path: path.as_posix()))

    def resolve_media(self, uri: str) -> Path | None:
        """Resolve one safe export-relative media URI across every root."""

        raw = unquote(str(uri or "").strip())
        relative = PurePosixPath(raw)
        if (
            not raw
            or "\\" in raw
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            return None
        for root in self.roots:
            path = root.joinpath(*relative.parts)
            try:
                resolved_root = root.resolve()
                resolved_path = path.resolve()
                if path.is_file() and (
                    resolved_path == resolved_root
                    or resolved_root in resolved_path.parents
                ):
                    return path
            except OSError:
                continue
        return None

    def message_records(
        self,
        *,
        folders: tuple[str, ...],
        counter: ContentKeyCounter,
        watermarks: Mapping[str, datetime] | None = None,
        limit: int | None = None,
    ) -> Iterator[MetaMessageRecord]:
        """Yield merged, chronological thread messages with stable derived ids.

        Numbered chunks are merged across all roots. Byte-identical repeated
        chunk files from overlapping multi-part exports are read once, while
        identical message rows retained inside a real chunk still receive
        occurrence ``0, 1, …`` ids from :class:`ContentKeyCounter`.
        """

        if limit is not None and int(limit) <= 0:
            return
        yielded = 0
        grouped = self._message_thread_directories(folders)
        for thread_key, directories in sorted(grouped.items()):
            chunks = self._thread_chunks(directories)
            rows: list[tuple[str, Mapping[str, Any]]] = []
            thread_title = ""
            participants: tuple[str, ...] = ()
            thread_path = ""
            seen_chunks: set[tuple[str, str]] = set()
            for chunk_path in chunks:
                digest = hashlib.sha256(chunk_path.read_bytes()).hexdigest()
                chunk_identity = (chunk_path.name, digest)
                if chunk_identity in seen_chunks:
                    continue
                seen_chunks.add(chunk_identity)
                chunk = load_json(chunk_path)
                if not isinstance(chunk, Mapping):
                    continue
                thread_title = thread_title or str(chunk.get("title") or "")
                thread_path = thread_path or str(chunk.get("thread_path") or "")
                if not participants:
                    participants = tuple(
                        str(item.get("name") or "")
                        for item in chunk.get("participants", ())
                        if isinstance(item, Mapping) and item.get("name")
                    )
                rows.extend(
                    (chunk_path.as_posix(), item)
                    for item in chunk.get("messages", ())
                    if isinstance(item, Mapping)
                )
            messages = _collapse_generations(rows)
            messages.sort(key=lambda item: _integer(item.get("timestamp_ms")))
            present_folders = tuple(
                dict.fromkeys(directory.parent.name for directory in directories)
            )
            thread_metadata = {
                "folder": present_folders[0],
                **({"folders": list(present_folders)} if len(present_folders) > 1 else {}),
                **({"thread_path": thread_path} if thread_path else {}),
            }
            for data in messages:
                timestamp_ms = _integer(data.get("timestamp_ms"))
                sent_at = timestamp_milliseconds(timestamp_ms)
                if sent_at is None:
                    continue
                sender = str(data.get("sender_name") or "")
                external_id = counter.key(
                    thread_key=thread_key,
                    timestamp_ms=timestamp_ms,
                    sender_key=sender,
                    content=message_content_projection(data),
                )
                watermark = (watermarks or {}).get(thread_key)
                if watermark is not None and sent_at <= watermark:
                    continue
                yield MetaMessageRecord(
                    data=data,
                    external_id=external_id,
                    thread_key=thread_key,
                    thread_title=thread_title,
                    thread_metadata=thread_metadata,
                    participants=participants,
                    sent_at=sent_at,
                    media=media_items(self, data),
                )
                yielded += 1
                if limit is not None and yielded >= max(0, int(limit)):
                    return

    def _message_thread_directories(
        self,
        folders: tuple[str, ...],
    ) -> dict[str, tuple[Path, ...]]:
        """Group matching thread directories from all roots by stable thread slug.

        The directory name is Meta's durable conversation slug; the containing
        folder is an export-run fact (accepting a message request moves the
        same conversation from ``message_requests`` to ``inbox``), so it must
        never enter the thread identity — it rides thread metadata instead.
        Directories append in the declared-folder order, so the first entry's
        folder is the highest-priority home when one thread appears in two.
        """

        grouped: dict[str, list[Path]] = {}
        for root in self.roots:
            base = root / self.activity_directory / "messages"
            for folder in folders:
                directory = base / folder
                if not directory.is_dir():
                    continue
                for thread in sorted(directory.iterdir(), key=lambda path: path.name):
                    if thread.is_dir():
                        grouped.setdefault(thread.name, []).append(thread)
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _thread_chunks(directories: tuple[Path, ...]) -> tuple[Path, ...]:
        """Return numbered message files in numeric chunk order then path order."""

        chunks = [
            path
            for directory in directories
            for path in directory.iterdir()
            if path.is_file() and _MESSAGE_FILE.match(path.name)
        ]
        return tuple(
            sorted(
                chunks,
                key=lambda path: (
                    int(_MESSAGE_FILE.match(path.name).group(1)),  # type: ignore[union-attr]
                    path.as_posix(),
                ),
            )
        )


def decode_message(
    record: MetaMessageRecord,
    *,
    platform: str,
    own_name: str,
    identity_metadata: Mapping[str, Any] | None = None,
) -> ParsedMessage:
    """Decode one shared-dialect record onto messaging's neutral DTO."""

    data = record.data
    sender_name = str(data.get("sender_name") or "Meta User")
    handle_metadata = dict(identity_metadata or {})
    sender = ParsedHandle(
        platform=platform,
        value=sender_name,
        display_name=sender_name,
        metadata=handle_metadata,
    )
    recipients = tuple(
        ParsedRecipient(
            handle=ParsedHandle(
                platform=platform,
                value=name,
                display_name=name,
                metadata=handle_metadata,
            )
        )
        for name in record.participants
        if name and name != sender_name
    )
    reactions = [
        {
            "reaction": str(item.get("reaction") or ""),
            "actor": str(item.get("actor") or ""),
        }
        for item in data.get("reactions", ())
        if isinstance(item, Mapping)
    ]
    metadata = {
        "source": f"{platform}_takeout",
        "thread": dict(record.thread_metadata),
        "sender_identity": handle_metadata,
        **({"reactions": reactions} if reactions else {}),
    }
    for key in ("share", "sticker", "ip", "call_duration"):
        if data.get(key) is not None:
            metadata[key] = data[key]
    modality = "group" if len(record.participants) > 2 else "direct"
    return ParsedMessage(
        external_id=record.external_id,
        platform=platform,
        direction="outbound" if sender_name == own_name else "inbound",
        sender=sender,
        recipients=recipients,
        sent_at=record.sent_at,
        thread=ParsedThread(
            external_id=record.thread_key,
            modality=modality,
            title=record.thread_title,
            metadata=dict(record.thread_metadata),
        ),
        body=body_part(text=str(data.get("content") or ""), media=record.media),
        metadata=metadata,
    )


def media_items(export: MetaExport, data: Mapping[str, Any]) -> tuple[MediaItem, ...]:
    """Resolve every Meta message media item, preserving missing files as markers."""

    items: list[MediaItem] = []
    for key in _MEDIA_KEYS:
        raw_items = data.get(key, ())
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            uri = str(raw_item.get("uri") or "")
            path = export.resolve_media(uri)
            content: bytes | None = None
            if path is not None:
                try:
                    content = path.read_bytes()
                except OSError:
                    content = None
            name = str(raw_item.get("filename") or "") or PurePosixPath(uri).name
            mime = str(raw_item.get("mime_type") or "")
            if not mime:
                mime = mimetypes.guess_type(name or uri)[0] or "application/octet-stream"
            items.append(MediaItem(mime=mime, name=name, content=content))
    return tuple(items)


def _collapse_generations(rows: list[tuple[str, Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """Keep one generation's copy of each message across merged export trees.

    Chunk-level dedup drops only byte-identical files; two export *generations*
    of one thread differ by a few appended messages, so every shared row would
    otherwise land once per generation under spurious ``occurrence`` ids. Rows
    group by identity tuple; per tuple, the single chunk file holding the most
    copies wins — a true duplicate is N copies in one file (preserved), while
    cross-chunk repeats are generation overlap (collapsed). Ties break on the
    lexicographically last chunk path, deterministically.
    """

    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for chunk_key, row in rows:
        identity = json.dumps(
            [
                _integer(row.get("timestamp_ms")),
                str(row.get("sender_name") or ""),
                message_content_projection(row),
            ],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        grouped.setdefault(identity, {}).setdefault(chunk_key, []).append(row)
    kept: list[Mapping[str, Any]] = []
    for chunks in grouped.values():
        best = max(chunks, key=lambda key: (len(chunks[key]), key))
        kept.extend(chunks[best])
    return kept


def _integer(value: Any) -> int:
    """Return an integer ordering value, using zero for malformed input."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
