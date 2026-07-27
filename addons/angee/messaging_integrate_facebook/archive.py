"""Facebook-specific layout over the shared Meta export dialect."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from angee.messaging.backup_ingest import ContentKeyCounter
from angee.messaging_integrate_meta.export import (
    MetaExport,
    MetaMessageRecord,
    load_json,
    timestamp_seconds,
)

ACTIVITY_DIRECTORY = "your_facebook_activity"
MESSAGE_FOLDERS = (
    "inbox",
    "archived_threads",
    "filtered_threads",
    "message_requests",
    "e2ee_cutover",
)
MARKER_RELATIVE_PATHS = (
    "personal_information/profile_information/profile_information.json",
    "connections/friends/your_friends.json",
)
"""Activity-relative paths whose presence proves a Facebook export tree."""
MARKER_FILENAMES = (
    *(PurePosixPath(path).name for path in MARKER_RELATIVE_PATHS),
    "message_1.json",
)
"""Marker basenames for probing an already-extracted export on disk."""


@dataclass(frozen=True)
class FacebookPostRecord:
    """One id-assigned own post or album photo from the archive."""

    data: Mapping[str, Any]
    external_id: str
    thread_key: str
    thread_title: str
    sent_at: datetime
    album: str = ""


@dataclass(frozen=True)
class FacebookCommentRecord:
    """One own comment from Facebook's comments section."""

    data: Mapping[str, Any]
    sent_at: datetime


@dataclass(frozen=True)
class FacebookConnectionRecord:
    """One current or removed Facebook friend fact."""

    name: str
    connected_at: datetime | None
    provenance: str


class FacebookArchive:
    """Read-only view of all Facebook export trees below one root."""

    def __init__(self, root: Path | str) -> None:
        self.export = MetaExport(
            root,
            activity_directory=ACTIVITY_DIRECTORY,
            root_prefix="facebook-",
        )

    def own_name(self) -> str:
        """Return the export owner's profile name, or an empty fallback."""

        path = self.export.first_path(
            ACTIVITY_DIRECTORY,
            "personal_information",
            "profile_information",
            "profile_information.json",
        )
        if path is None:
            return ""
        data = load_json(path)
        if not isinstance(data, Mapping):
            return ""
        profile = data.get("profile_v2")
        if not isinstance(profile, Mapping):
            return ""
        name = profile.get("name")
        if isinstance(name, Mapping):
            return str(name.get("full_name") or "")
        return str(name or "")

    def messages(
        self,
        *,
        counter: ContentKeyCounter,
        watermarks: Mapping[str, datetime] | None = None,
        limit: int | None = None,
    ) -> Iterator[MetaMessageRecord]:
        """Yield shared-dialect message records from every Facebook folder."""

        return self.export.message_records(
            folders=MESSAGE_FOLDERS,
            counter=counter,
            watermarks=watermarks,
            limit=limit,
        )

    def posts(self, *, counter: ContentKeyCounter) -> list[FacebookPostRecord]:
        """Return own posts and album photos with deterministic external ids."""

        own_name = self.own_name()
        records: list[FacebookPostRecord] = []
        paths = self.export.glob(
            ACTIVITY_DIRECTORY,
            "posts",
            pattern="your_posts*.json",
        )
        for path in _unique_files(paths):
            data = load_json(path)
            values = data if isinstance(data, list) else ()
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                record = _post_record(
                    value,
                    counter=counter,
                    sender=own_name,
                    thread_key="feed:posts",
                    thread_title=f"{own_name}'s Posts" if own_name else "Facebook Posts",
                )
                if record is not None:
                    records.append(record)

        album_paths = self.export.glob(
            ACTIVITY_DIRECTORY,
            "posts",
            "album",
            pattern="*.json",
        )
        for path in _unique_files(album_paths):
            data = load_json(path)
            if not isinstance(data, Mapping):
                continue
            # Album files are renumbered when albums are added; the album name is
            # the stable identity, the file stem only a last-resort fallback.
            album_name = str(data.get("name") or path.stem)
            thread_key = f"feed:album:{album_name}"
            for value in data.get("photos", ()):
                if not isinstance(value, Mapping):
                    continue
                record = _post_record(
                    value,
                    counter=counter,
                    sender=own_name,
                    thread_key=thread_key,
                    thread_title=album_name,
                    album=album_name,
                    timestamp_field="creation_timestamp",
                )
                if record is not None:
                    records.append(record)
        return sorted(records, key=lambda record: (record.sent_at, record.external_id))

    def comments(self) -> list[FacebookCommentRecord]:
        """Return chronological own comments from every comments section file."""

        records: list[FacebookCommentRecord] = []
        paths = self.export.paths(
            ACTIVITY_DIRECTORY,
            "comments_and_reactions",
            "comments.json",
        )
        for path in _unique_files(paths):
            data = load_json(path)
            values = data.get("comments_v2", ()) if isinstance(data, Mapping) else ()
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                sent_at = timestamp_seconds(value.get("timestamp"))
                if sent_at is not None:
                    records.append(FacebookCommentRecord(data=value, sent_at=sent_at))
        return sorted(records, key=lambda record: (record.sent_at, str(record.data)))

    def connections(self) -> list[FacebookConnectionRecord]:
        """Return current and removed friend facts across all export roots."""

        records: list[FacebookConnectionRecord] = []
        specs = (
            ("your_friends.json", "friends_v2", "facebook_takeout_friend"),
            ("removed_friends.json", "deleted_friends_v2", "facebook_takeout_removed_friend"),
        )
        for filename, key, provenance in specs:
            paths = self.export.paths(
                ACTIVITY_DIRECTORY,
                "connections",
                "friends",
                filename,
            )
            for path in _unique_files(paths):
                data = load_json(path)
                values = data.get(key, ()) if isinstance(data, Mapping) else ()
                for value in values:
                    if not isinstance(value, Mapping):
                        continue
                    name = str(value.get("name") or "").strip()
                    if name:
                        records.append(
                            FacebookConnectionRecord(
                                name=name,
                                connected_at=timestamp_seconds(value.get("timestamp")),
                                provenance=provenance,
                            )
                        )
        return sorted(
            records,
            key=lambda record: (
                record.name.casefold(),
                record.provenance,
                record.connected_at.timestamp() if record.connected_at is not None else -1,
            ),
        )


def _post_record(
    data: Mapping[str, Any],
    *,
    counter: ContentKeyCounter,
    sender: str,
    thread_key: str,
    thread_title: str,
    album: str = "",
    timestamp_field: str = "timestamp",
) -> FacebookPostRecord | None:
    """Build one post record when its second timestamp is valid."""

    sent_at = timestamp_seconds(data.get(timestamp_field))
    if sent_at is None:
        return None
    timestamp_ms = int(sent_at.timestamp() * 1000)
    return FacebookPostRecord(
        data=data,
        external_id=counter.key(
            thread_key=thread_key,
            timestamp_ms=timestamp_ms,
            sender_key=sender,
            content=_post_content_projection(data, album),
        ),
        thread_key=thread_key,
        thread_title=thread_title,
        sent_at=sent_at,
        album=album,
    )


def _post_content_projection(data: Mapping[str, Any], album: str) -> dict[str, Any]:
    """Return the author-written identity content of one post or album photo.

    Derived post ids digest this projection, never the whole record — Meta's
    export revisions add envelope keys and engagement mutates, and neither may
    shift a post's identity. Media identifies by attachment basename (the
    fbid-named file), stable across export layout churn.
    """

    projection: dict[str, Any] = {"title": str(data.get("title") or "")}
    if album:
        projection["album"] = album
    description = str(data.get("description") or "")
    if description:
        projection["description"] = description
    raw_texts = data.get("data")
    texts = [
        str(item.get("post") or "")
        for item in (raw_texts if isinstance(raw_texts, list) else ())
        if isinstance(item, Mapping)
    ]
    if texts:
        projection["texts"] = texts
    media: list[str] = []
    uri = str(data.get("uri") or "")
    if uri:
        media.append(PurePosixPath(uri).name)
    raw_attachments = data.get("attachments")
    for attachment in raw_attachments if isinstance(raw_attachments, list) else ():
        if not isinstance(attachment, Mapping):
            continue
        raw_items = attachment.get("data")
        for item in raw_items if isinstance(raw_items, list) else ():
            if isinstance(item, Mapping) and isinstance(item.get("media"), Mapping):
                media.append(PurePosixPath(str(item["media"].get("uri") or "")).name)
    if media:
        projection["media"] = media
    return projection


def _unique_files(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Drop same-named overlap copies while preserving real numbered sections."""

    unique: list[Path] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        identity = (path.name, digest)
        if identity not in seen:
            seen.add(identity)
            unique.append(path)
    return tuple(unique)
