"""Reader over Apple's ``sms.db`` (SMS + iMessage) inside an iPhone backup.

An **unencrypted** iPhone backup stores files content-addressed:
:class:`angee.integrate_iphone.backup.IosBackup` resolves ``(domain, relativePath)``
to a physical blob. Apple Messages keeps *both* regular SMS and iMessage in one
Core Data-adjacent SQLite store — ``HomeDomain`` / ``Library/SMS/sms.db`` — with
attachments under ``MediaDomain`` / ``Library/SMS/Attachments/…``. Timestamps are
Apple/Core Data time: iOS 11+ writes ``message.date`` in *nanoseconds* since
2001-01-01 UTC, older iOS in *seconds*; the two are told apart by magnitude.

This reader maps store rows onto the neutral :class:`~.parser.ChatMessage`; every
identity rule (phone/email handles, the globally-unique message guid as the ingest
key, media markers) is :mod:`.parser`'s, and the batching + resume loop is
:mod:`angee.messaging.backup_ingest`'s — so this module owns only the ``sms.db``
schema and its date encoding. Newer iOS leaves ``message.text`` NULL and stores the
body in ``message.attributedBody``; :func:`~.attributed_body.attributed_body_text`
recovers it.
"""

from __future__ import annotations

import mimetypes
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from angee.integrate_iphone.backup import (
    SQLITE_HEADER_LENGTH,
    BackupError,
    IosBackup,
    is_sqlite_header,
)
from angee.messaging.backends import MediaItem
from angee.messaging_integrate_imessage.attributed_body import attributed_body_text
from angee.messaging_integrate_imessage.parser import ChatMessage

SMS_DOMAIN = "HomeDomain"
SMS_PATH = "Library/SMS/sms.db"
MEDIA_DOMAIN = "MediaDomain"

CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
"""Apple/Core Data reference date — ``message.date`` counts from this instant."""

_GROUP_STYLE = 43
"""Apple ``chat.style``: 43 = group, 45 = direct."""

_NANOSECOND_THRESHOLD = 1e11
"""``message.date`` above this magnitude is nanoseconds (iOS 11+), else seconds."""

# Normalize a raw ``message.date`` to Core Data *seconds* inside SQL, so ``since``
# and resume-watermark comparisons are unit-consistent regardless of the store's
# iOS-version encoding (the same magnitude test :meth:`_row_message` applies).
_DATE_SECONDS_SQL = (
    f"(CASE WHEN m.date > {_NANOSECOND_THRESHOLD:.0f} THEN m.date / 1000000000.0 ELSE m.date END)"
)

_MESSAGE_OPTIONAL = ("attributedBody", "associated_message_type", "item_type")
"""``message`` columns absent on older iOS — selected as ``NULL`` when missing."""

_CHAT_OPTIONAL = ("chat_identifier", "display_name", "style", "room_name")
"""``chat`` columns absent on older iOS — selected as ``NULL`` when missing."""


def sms_store_path(backup: IosBackup) -> Path | None:
    """Return the manifest-resolved ``sms.db`` blob in ``backup``, or ``None``."""

    return backup.blob_path(SMS_DOMAIN, SMS_PATH)


def has_sms_store(backup: IosBackup) -> bool:
    """Return whether ``backup`` carries a SQLite Messages store."""

    store = sms_store_path(backup)
    if store is None:
        return False
    try:
        with store.open("rb") as stream:
            return is_sqlite_header(stream.read(SQLITE_HEADER_LENGTH))
    except OSError:
        return False


class ImessageStore:
    """Reader over Apple's ``sms.db`` inside an :class:`IosBackup`."""

    def __init__(self, backup: IosBackup) -> None:
        self.backup = backup
        store = sms_store_path(backup)
        if store is None:
            # Close the backup's manifest connection we can no longer own.
            backup.close()
            raise BackupError("This backup contains no Messages store.")
        self._db = sqlite3.connect(f"file:{store}?mode=ro&immutable=1", uri=True)
        try:
            self._message_columns = self._table_columns("message")
            self._chat_columns = self._table_columns("chat")
        except Exception:
            # A store that passed the header check but is corrupt/locked can still
            # fail the first PRAGMA; close both connections here so a raising
            # __init__ never leaks the sockets the caller has no handle to close.
            self._db.close()
            backup.close()
            raise
        self._attachment_index: dict[Any, list[tuple[Any, Any, Any]]] = {}

    def close(self) -> None:
        """Close the Messages store and the backup manifest connection it owns."""

        self._db.close()
        self.backup.close()

    def messages(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        watermarks: dict[str, datetime] | None = None,
    ) -> Iterator[ChatMessage]:
        """Yield chat-ordered messages as the neutral shape, media bytes resolved.

        Tapbacks (``associated_message_type``) and system events (``item_type``)
        are skipped; a row with neither text nor media carries nothing to land.
        ``watermarks`` maps a chat guid to the newest already-imported instant in
        that chat; the filter runs in SQL — before :meth:`_row_message` resolves
        any media — so a resumed import skips the imported prefix instead of
        re-reading its media, the reason a very large history advances across the
        task time limit instead of restarting.
        """

        if watermarks:
            self._install_watermarks(watermarks)
        # Bucket every attachment's metadata by message id in one query; a
        # per-message lookup would be an N+1 across the whole history.
        self._attachment_index = self._index_attachments()

        query = ["SELECT " + ", ".join(self._select_columns())]
        query.append("FROM message m")
        query.append("JOIN chat_message_join cmj ON cmj.message_id = m.ROWID")
        query.append("JOIN chat c ON c.ROWID = cmj.chat_id")
        query.append("LEFT JOIN handle h ON h.ROWID = m.handle_id")
        if watermarks:
            query.append(f"LEFT JOIN temp._wm ON temp._wm.chat_key = {self._chat_key_sql()}")

        conditions: list[str] = []
        params: list[Any] = []
        if "associated_message_type" in self._message_columns:
            conditions.append("(m.associated_message_type IS NULL OR m.associated_message_type = 0)")
        if "item_type" in self._message_columns:
            conditions.append("(m.item_type IS NULL OR m.item_type = 0)")
        if watermarks:
            conditions.append(f"(temp._wm.since IS NULL OR {_DATE_SECONDS_SQL} >= temp._wm.since)")
        if since is not None:
            conditions.append(f"{_DATE_SECONDS_SQL} >= ?")
            params.append(self._core_data_seconds(since))
        if conditions:
            query.append("WHERE " + " AND ".join(conditions))
        query.append("ORDER BY c.ROWID, m.date, m.ROWID")
        if limit is not None:
            query.append("LIMIT ?")
            params.append(limit)

        for row in self._db.execute("\n".join(query), params):
            message = self._row_message(row)
            if message is not None:
                yield message

    def _table_columns(self, table: str) -> set[str]:
        """Return the column names present on ``table`` (probed once).

        Older iOS lacks some optional columns; probing lets the SELECT ask only
        for columns that exist and substitute ``NULL`` for the rest, keeping the
        row shape stable.
        """

        return {str(row[1]) for row in self._db.execute(f"PRAGMA table_info({table})")}

    def _select_columns(self) -> list[str]:
        """Return the fixed-order SELECT expressions :meth:`_row_message` unpacks."""

        return [
            "m.ROWID AS message_rowid",
            "m.guid AS message_guid",
            "m.is_from_me",
            "m.date",
            "m.text",
            self._optional("m", "attributedBody", self._message_columns),
            "m.service",
            self._optional("m", "associated_message_type", self._message_columns),
            self._optional("m", "item_type", self._message_columns),
            "h.id AS handle_value",
            "c.ROWID AS chat_rowid",
            "c.guid AS chat_guid",
            self._optional("c", "chat_identifier", self._chat_columns),
            self._optional("c", "display_name", self._chat_columns),
            self._optional("c", "style", self._chat_columns),
            self._optional("c", "room_name", self._chat_columns),
        ]

    @staticmethod
    def _optional(alias: str, column: str, present: set[str]) -> str:
        """Return ``<alias>.<column>`` when present, else ``NULL AS <column>``."""

        return f"{alias}.{column}" if column in present else f"NULL AS {column}"

    def _chat_key_sql(self) -> str:
        """Return the SQL for a chat's thread key.

        Uses the same coalesce order :meth:`_row_message` builds the stored thread
        key from — guid, then ``chat_identifier`` when present, then a rowid
        fallback — so the resume-watermark join matches the key that was recorded
        at ingest instead of only chats whose ``guid`` is non-empty.
        """

        parts = ["NULLIF(c.guid, '')"]
        if "chat_identifier" in self._chat_columns:
            parts.append("NULLIF(c.chat_identifier, '')")
        return "COALESCE(" + ", ".join(parts) + ", 'ios-chat:' || c.ROWID)"

    def _row_message(self, row: tuple[Any, ...]) -> ChatMessage | None:
        """Map one joined ``message`` row onto the neutral shape (skip empty rows)."""

        (
            rowid,
            message_guid,
            is_from_me,
            date,
            text,
            attributed,
            service,
            _associated_type,
            _item_type,
            handle_value,
            chat_rowid,
            chat_guid,
            chat_identifier,
            display_name,
            style,
            room_name,
        ) = row

        body = str(text or "") or attributed_body_text(attributed)
        media = self._attachments(rowid)
        if not body and not media:
            return None

        chat = str(chat_guid or "") or str(chat_identifier or "") or f"ios-chat:{chat_rowid}"
        from_me = bool(is_from_me)
        group: bool | None = None
        if style is not None or room_name:
            group = style == _GROUP_STYLE or bool(room_name)
        return ChatMessage(
            chat_guid=chat,
            message_guid=str(message_guid or ""),
            fallback_id=f"ios:{rowid}",
            chat_name=str(display_name or ""),
            group=group,
            sender_value="" if from_me else str(handle_value or ""),
            sender_name="",
            from_me=from_me,
            timestamp=self._timestamp(date),
            text=body,
            service=str(service or ""),
            media=media,
        )

    def _index_attachments(self) -> dict[Any, list[tuple[Any, Any, Any]]]:
        """Bucket every attachment's metadata by message id in one query.

        Avoids an N+1 per-message lookup: a 90k-message backup would otherwise
        issue 90k extra round-trips, most returning nothing. Only metadata is
        materialized — a few MB even for tens of thousands of attachments — so
        blob bytes stay lazy, read per yielded message in :meth:`_media_item`.
        A store without the attachment tables yields an empty index.
        """

        index: dict[Any, list[tuple[Any, Any, Any]]] = {}
        try:
            rows = self._db.execute(
                "SELECT maj.message_id, a.filename, a.mime_type, a.transfer_name "
                "FROM message_attachment_join maj "
                "JOIN attachment a ON a.ROWID = maj.attachment_id "
                "ORDER BY maj.message_id, maj.attachment_id"
            )
        except sqlite3.OperationalError:
            return index
        for message_id, filename, mime_type, transfer_name in rows:
            index.setdefault(message_id, []).append((filename, mime_type, transfer_name))
        return index

    def _attachments(self, message_rowid: Any) -> tuple[MediaItem, ...]:
        """Resolve one message's attachments from the pre-built index (markers when missing)."""

        rows = self._attachment_index.get(message_rowid, ())
        items = [self._media_item(filename, mime_type, transfer_name) for filename, mime_type, transfer_name in rows]
        return tuple(item for item in items if item is not None)

    def _media_item(self, filename: Any, mime_type: Any, transfer_name: Any) -> MediaItem | None:
        """Resolve one attachment reference from the backup (marker when missing).

        Apple records ``~/Library/SMS/Attachments/…`` paths; the leading ``~/`` is
        stripped to the ``MediaDomain``-relative path the backup manifest keys on.
        """

        if not filename:
            return None
        relative = str(filename)
        if relative.startswith("~/"):
            relative = relative[2:]
        content = self.backup.read(MEDIA_DOMAIN, relative)
        name = str(transfer_name or "") or PurePosixPath(relative).name
        mime = str(mime_type or "") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        return MediaItem(mime=mime, name=name, content=content)

    def _install_watermarks(self, watermarks: dict[str, datetime]) -> None:
        """Load per-chat resume watermarks into a temp table for the messages join.

        A temp table lives in the connection's temp database, so it is writable
        even though the store opens read-only and immutable. Each ``datetime`` is
        stored in Core Data seconds, the unit :data:`_DATE_SECONDS_SQL` normalizes
        the row date to.
        """

        self._db.execute("DROP TABLE IF EXISTS temp._wm")
        self._db.execute("CREATE TEMP TABLE _wm(chat_key TEXT PRIMARY KEY, since REAL)")
        self._db.executemany(
            "INSERT OR REPLACE INTO _wm(chat_key, since) VALUES (?, ?)",
            sorted(
                (chat_key, self._core_data_seconds(instant))
                for chat_key, instant in watermarks.items()
            ),
        )

    def _timestamp(self, date: Any) -> datetime | None:
        """Convert a raw ``message.date`` to a UTC instant (ns or seconds, by magnitude)."""

        if date is None:
            return None
        seconds = float(date)
        if seconds > _NANOSECOND_THRESHOLD:
            seconds /= 1e9
        return CORE_DATA_EPOCH + timedelta(seconds=seconds)

    @staticmethod
    def _core_data_seconds(instant: datetime) -> float:
        """Return ``instant`` as Core Data seconds (seconds since 2001-01-01 UTC)."""

        return (instant - CORE_DATA_EPOCH).total_seconds()


def open_sms_store(backup_dir: Path | str) -> ImessageStore:
    """Open the Messages store inside one unencrypted iPhone backup directory."""

    return ImessageStore(IosBackup(backup_dir))
