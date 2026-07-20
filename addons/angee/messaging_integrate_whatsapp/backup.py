"""WhatsApp iOS device-backup import — stdlib readers + the shared ingest drive.

An **unencrypted** iPhone backup (Finder/iTunes) stores files content-addressed:
``Manifest.db`` maps ``(domain, relativePath)`` to a ``fileID`` whose bytes live
at ``<backup>/<fileID[:2]>/<fileID>``; when a manifest row is missing the id is
derivable as ``sha1(f"{domain}-{relativePath}")``. WhatsApp's data lives in the
``AppDomainGroup-group.net.whatsapp.WhatsApp.shared`` domain: ``ChatStorage.sqlite``
(a Core Data store — timestamps count seconds from 2001-01-01 UTC) plus the
media files its ``ZWAMEDIAITEM`` rows point at.

:class:`IosBackup` and :class:`ChatStorage` only *read* those shapes into the
neutral :class:`~.parser.ChatMessage`; every identity rule (JID normalization,
the ``<chat>/<stanza>`` convergence key, the ``ios:<pk>`` synthetic fallback,
media markers) is :mod:`.parser`'s, so a backup import and a later live pairing
land on the same rows. :class:`BackupImporter` batches through
``Message.objects.ingest`` — idempotent, therefore resumable: re-running an
interrupted import converges instead of duplicating.
"""

from __future__ import annotations

import hashlib
import mimetypes
import plistlib
import sqlite3
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from django.apps import apps
from django.db.models import Max
from rebac import system_context

from angee.messaging.backends import MediaItem
from angee.messaging_integrate_whatsapp.parser import ChatMessage, bare_jid, parsed_message

WHATSAPP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
WHATSAPP_SMB_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared"
CHAT_STORAGE_PATH = "ChatStorage.sqlite"

WHATSAPP_DOMAINS: tuple[str, ...] = (WHATSAPP_DOMAIN, WHATSAPP_SMB_DOMAIN)
"""Both WhatsApp app domains an iPhone backup may carry — personal then business.

WhatsApp (personal) and WhatsApp Business (SMB) install as separate iOS apps,
each with its own app-group domain and its own ``ChatStorage.sqlite``, so a
device with both accounts backs up two independent stores."""

CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

_SKIPPED_MESSAGE_TYPES = (6, 14)  # group-system events and revoked messages
_SQLITE_HEADER = b"SQLite format 3\x00"

SQLITE_HEADER_LENGTH = len(_SQLITE_HEADER)
"""Bytes a bounded chat-store header check reads — never the store body."""


class BackupError(Exception):
    """The backup directory cannot serve this import (missing, encrypted, foreign)."""


class IosBackup:
    """Read-only view over one unencrypted iPhone backup directory."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        manifest_db = self.path / "Manifest.db"
        if not manifest_db.exists():
            raise BackupError(f"{self.path} is not an iPhone backup (no Manifest.db).")
        self._reject_encrypted()
        self._manifest = sqlite3.connect(f"file:{manifest_db}?mode=ro", uri=True)

    def _reject_encrypted(self) -> None:
        """Fail loudly on an encrypted backup — decryption is out of scope."""

        manifest_plist = self.path / "Manifest.plist"
        if not manifest_plist.exists():
            return
        try:
            manifest = plistlib.loads(manifest_plist.read_bytes())
        except plistlib.InvalidFileException as exc:
            raise BackupError(f"{manifest_plist} could not be parsed.") from exc
        if manifest.get("IsEncrypted"):
            raise BackupError(
                "This iPhone backup is encrypted. Create an unencrypted backup "
                "(Finder → uncheck 'Encrypt local backup') and import that."
            )

    def blob_path(self, domain: str, relative_path: str) -> Path | None:
        """Return the on-disk blob for one backed-up file, or ``None``.

        Prefers the manifest row; falls back to the deterministic
        ``sha1(domain-relativePath)`` id backups omit manifest rows for.
        """

        blob = self.path / self.blob_relative_path(domain, relative_path)
        return blob if blob.exists() else None

    def blob_relative_path(self, domain: str, relative_path: str) -> PurePosixPath:
        """Return the fanout-relative blob location — the layout's single owner.

        iOS backups shard blobs as ``<fileID[:2]>/<fileID>``; every consumer
        (filesystem or archive member lookup) composes this instead of
        re-deriving the fanout.
        """

        file_id = self.blob_id(domain, relative_path)
        return PurePosixPath(file_id[:2], file_id)

    def blob_id(self, domain: str, relative_path: str) -> str:
        """Return one file's manifest id or its deterministic fallback id."""

        row = self._manifest.execute(
            "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?",
            (domain, relative_path),
        ).fetchone()
        return str(row[0]) if row else hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()

    def chat_storage_path(self, domain: str = WHATSAPP_DOMAIN) -> Path | None:
        """Return one WhatsApp app domain's manifest-resolved chat-store blob."""

        return self.blob_path(domain, CHAT_STORAGE_PATH)

    def has_chat_storage(self, domain: str = WHATSAPP_DOMAIN) -> bool:
        """Return whether ``domain``'s manifest-resolved chat store has a SQLite header.

        Recognition intentionally reads only SQLite's fixed 16-byte header;
        parsing messages remains :class:`ChatStorage`'s execution-time job.
        """

        store = self.chat_storage_path(domain)
        if store is None:
            return False
        try:
            with store.open("rb") as stream:
                return self.is_chat_storage_header(stream.read(len(_SQLITE_HEADER)))
        except OSError:
            return False

    @staticmethod
    def is_chat_storage_header(value: bytes) -> bool:
        """Return whether ``value`` begins with SQLite's fixed file header."""

        return value.startswith(_SQLITE_HEADER)

    def read(self, domain: str, relative_path: str) -> bytes | None:
        """Return one backed-up file's bytes, or ``None`` when absent."""

        blob = self.blob_path(domain, relative_path)
        return blob.read_bytes() if blob is not None else None

    def close(self) -> None:
        self._manifest.close()


class ChatStorage:
    """Reader over WhatsApp's ``ChatStorage.sqlite`` inside an :class:`IosBackup`."""

    def __init__(self, backup: IosBackup, *, domain: str = WHATSAPP_DOMAIN) -> None:
        self.backup = backup
        self.domain = domain
        store = backup.chat_storage_path(domain)
        if store is None:
            # Close the backup's manifest connection we can no longer own.
            backup.close()
            raise BackupError("This backup contains no WhatsApp chat store.")
        self._db = sqlite3.connect(f"file:{store}?mode=ro&immutable=1", uri=True)

    def close(self) -> None:
        """Close the chat store and the backup manifest connection it owns."""

        self._db.close()
        self.backup.close()

    def messages(
        self,
        *,
        own_jid: str = "",
        chats: tuple[str, ...] = (),
        since: datetime | None = None,
        limit: int | None = None,
        watermarks: dict[str, float] | None = None,
    ) -> Iterator[ChatMessage]:
        """Yield chat-ordered messages as the neutral shape, media bytes resolved.

        ``own_jid`` attributes outbound rows (a paired channel already knows it;
        ``--own-jid`` supplies it otherwise); without one, outbound messages land
        senderless rather than misattributed. System events and revoked rows are
        skipped; a row with neither text nor media carries nothing to land.

        ``watermarks`` maps a bare, lowercased chat JID to the CoreData-seconds of
        the newest already-imported message in that chat. The filter runs in SQL,
        before :meth:`_row_message` resolves any media, so a resumed import skips
        the imported prefix instead of re-reading its media — the reason a very
        large history advances across the task time limit instead of restarting.
        """

        if watermarks:
            self._install_watermarks(watermarks)

        query = [
            "SELECT m.Z_PK, m.ZSTANZAID, m.ZISFROMME, m.ZMESSAGEDATE, m.ZTEXT,",
            "       m.ZMESSAGETYPE, m.ZFROMJID, s.ZCONTACTJID, s.ZPARTNERNAME,",
            "       gm.ZMEMBERJID, gm.ZCONTACTNAME, mi.ZMEDIALOCALPATH, mi.ZTITLE",
            "FROM ZWAMESSAGE m",
            "JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION",
            "LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER",
            "LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM",
        ]
        if watermarks:
            query.append("LEFT JOIN temp._wm ON temp._wm.jid = lower(s.ZCONTACTJID)")
        query.append("WHERE (m.ZMESSAGETYPE IS NULL OR m.ZMESSAGETYPE NOT IN (?, ?))")
        params: list[Any] = list(_SKIPPED_MESSAGE_TYPES)
        if watermarks:
            query.append("AND (temp._wm.since IS NULL OR m.ZMESSAGEDATE >= temp._wm.since)")
        if chats:
            normalized = tuple(bare_jid(chat) for chat in chats)
            query.append(f"AND lower(s.ZCONTACTJID) IN ({','.join('?' * len(normalized))})")
            params.extend(normalized)
        if since is not None:
            query.append("AND m.ZMESSAGEDATE >= ?")
            params.append((since - CORE_DATA_EPOCH).total_seconds())
        query.append("ORDER BY s.Z_PK, m.ZMESSAGEDATE, m.Z_PK")
        if limit is not None:
            query.append("LIMIT ?")
            params.append(limit)

        own = bare_jid(own_jid)
        for row in self._db.execute("\n".join(query), params):
            message = self._row_message(row, own_jid=own)
            if message is not None:
                yield message

    def _install_watermarks(self, watermarks: dict[str, float]) -> None:
        """Load per-chat resume watermarks into a temp table for the messages join.

        A temp table lives in the connection's temp database, so it is writable
        even though the chat store opens read-only and immutable.
        """

        self._db.execute("DROP TABLE IF EXISTS temp._wm")
        self._db.execute("CREATE TEMP TABLE _wm(jid TEXT PRIMARY KEY, since REAL)")
        self._db.executemany(
            "INSERT OR REPLACE INTO _wm(jid, since) VALUES (?, ?)", sorted(watermarks.items())
        )

    def _row_message(self, row: tuple[Any, ...], *, own_jid: str) -> ChatMessage | None:
        """Map one joined ZWAMESSAGE row onto the neutral shape."""

        (
            pk,
            stanza_id,
            is_from_me,
            message_date,
            text,
            _message_type,
            from_jid,
            chat_jid,
            partner_name,
            member_jid,
            member_name,
            media_path,
            media_title,
        ) = row
        from_me = bool(is_from_me)
        chat = str(chat_jid or "")
        if from_me:
            sender, sender_name = own_jid, ""
        elif member_jid:
            sender, sender_name = str(member_jid), str(member_name or "")
        else:
            sender, sender_name = str(from_jid or chat), str(partner_name or "")
        media = self._media_item(media_path, media_title)
        body = str(text or "")
        if not body and media is None:
            return None
        timestamp = None
        if message_date is not None:
            timestamp = CORE_DATA_EPOCH + timedelta(seconds=float(message_date))
        return ChatMessage(
            chat_jid=chat,
            stanza_id=str(stanza_id or ""),
            fallback_id=f"ios:{pk}",
            chat_name=str(partner_name or ""),
            sender_jid=sender,
            sender_name=sender_name,
            from_me=from_me,
            timestamp=timestamp,
            text=body,
            media=(media,) if media is not None else (),
        )

    def _media_item(self, media_path: Any, media_title: Any) -> MediaItem | None:
        """Resolve one media reference from the backup blobs (marker when missing).

        WhatsApp records ``Media/…`` paths; backups frequently nest the same
        files under ``Message/Media/…``, so both spellings are tried.
        """

        if not media_path:
            return None
        relative = str(media_path)
        content = self.backup.read(self.domain, relative)
        if content is None:
            content = self.backup.read(self.domain, f"Message/{relative}")
        name = Path(relative).name
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        del media_title  # ZTITLE names albums/captions handled by ZTEXT already
        return MediaItem(mime=mime, name=name, content=content)


class BackupImporter:
    """Drive one backup's messages through the shared ingest path, in batches."""

    def __init__(
        self,
        channel: Any,
        chat_storage: ChatStorage,
        *,
        own_jid: str = "",
        chats: tuple[str, ...] = (),
        since: datetime | None = None,
        limit: int | None = None,
        batch_size: int = 500,
        max_batch_bytes: int = 64_000_000,
        dry_run: bool = False,
        resume: bool = False,
    ) -> None:
        self.channel = channel
        self.chat_storage = chat_storage
        self.own_jid = own_jid or str(channel.subscription_state.get("own_jid") or "")
        self.chats = chats
        self.since = since
        self.limit = limit
        self.batch_size = max(1, int(batch_size))
        self.max_batch_bytes = max(1, int(max_batch_bytes))
        self.dry_run = dry_run
        self.resume = resume

    def _resume_watermarks(self) -> dict[str, float]:
        """Return each chat's newest already-imported timestamp, in CoreData seconds.

        Empty unless resuming. Keyed by the bare, lowercased chat JID recovered
        from the thread's ``chat:<channel>:<jid>`` external id, so the reader can
        skip the imported prefix per chat. A global ``since`` would instead drop
        older messages in chats a prior interrupted run never reached; per-chat
        watermarks converge on the last imported row and advance from there.
        """

        if not self.resume or self.dry_run:
            return {}
        message_model = apps.get_model("messaging", "Message")
        prefix = f"chat:{self.channel.pk}:"
        watermarks: dict[str, float] = {}
        with system_context(reason="messaging_integrate_whatsapp.backup_import.watermarks"):
            rows = (
                message_model._base_manager.filter(
                    thread__channel=self.channel, sent_at__isnull=False
                )
                .values("thread__external_id")
                .annotate(latest=Max("sent_at"))
            )
            for row in rows:
                external_id = str(row["thread__external_id"] or "")
                if external_id.startswith(prefix) and row["latest"] is not None:
                    chat_jid = bare_jid(external_id[len(prefix) :])
                    if chat_jid:
                        watermarks[chat_jid] = (row["latest"] - CORE_DATA_EPOCH).total_seconds()
        return watermarks

    def run(self, *, on_batch: Any = None) -> int:
        """Import (or, dry-run, count) every selected message; return the total.

        A batch flushes at ``batch_size`` messages **or** ``max_batch_bytes`` of
        buffered media, whichever comes first — the 500-message default is fine
        for text but a run of large videos would otherwise hold gigabytes of
        media bytes resident before the first ingest.
        """

        message_model = apps.get_model("messaging", "Message")
        total = 0
        batch: list[Any] = []
        batch_bytes = 0

        def flush() -> None:
            nonlocal total, batch_bytes
            if not batch:
                return
            if not self.dry_run:
                with system_context(reason="messaging_integrate_whatsapp.backup_import"):
                    message_model.objects.ingest(
                        batch,
                        channel=self.channel,
                        message_kind=message_model.MessageKind.CHAT,
                        quote_edges=False,
                    )
            total += len(batch)
            if on_batch is not None:
                on_batch(total)
            batch.clear()
            batch_bytes = 0

        for message in self.chat_storage.messages(
            own_jid=self.own_jid,
            chats=self.chats,
            since=self.since,
            limit=self.limit,
            watermarks=self._resume_watermarks(),
        ):
            batch.append(parsed_message(message))
            batch_bytes += sum(len(item.content) for item in message.media if item.content)
            if len(batch) >= self.batch_size or batch_bytes >= self.max_batch_bytes:
                flush()
        flush()
        return total


def open_chat_storage(backup_dir: Path | str, *, domain: str = WHATSAPP_DOMAIN) -> ChatStorage:
    """Open one WhatsApp app domain's chat store inside one backup directory."""

    return ChatStorage(IosBackup(backup_dir), domain=domain)


def whatsapp_domains(backup: IosBackup) -> tuple[str, ...]:
    """Return the WhatsApp app domains whose chat store ``backup`` carries.

    Personal and business (SMB) install as separate apps, so a device with both
    accounts yields two independent stores. Recognition reads only each store's
    fixed SQLite header, never its body — the caller maps each domain to its own
    :class:`~messaging.Channel`.
    """

    return tuple(domain for domain in WHATSAPP_DOMAINS if backup.has_chat_storage(domain))


def import_backup(
    channel: Any,
    backup_dir: Path | str,
    *,
    domain: str = WHATSAPP_DOMAIN,
    own_jid: str = "",
    chats: tuple[str, ...] = (),
    since: datetime | None = None,
    limit: int | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    resume: bool = False,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Open and import one WhatsApp app domain's backup through :class:`BackupImporter`.

    This is the shared importer facade for the management command and workflow
    extractor. It owns the chat-store lifetime so every client closes both
    SQLite connections on success or failure. ``domain`` selects the personal or
    business (SMB) store; the two accounts import independently into their own
    channels. ``resume`` skips each chat's already-imported prefix so an import
    interrupted by the task time limit advances on re-run instead of restarting.
    """

    chat_storage = open_chat_storage(backup_dir, domain=domain)
    try:
        importer = BackupImporter(
            channel,
            chat_storage,
            own_jid=own_jid,
            chats=chats,
            since=since,
            limit=limit,
            batch_size=batch_size,
            dry_run=dry_run,
            resume=resume,
        )
        return importer.run(on_batch=on_batch)
    finally:
        chat_storage.close()
