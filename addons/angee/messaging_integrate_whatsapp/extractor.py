"""WhatsApp iPhone-backup client for the archive workflow bridge."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast

from angee.messaging_integrate_whatsapp import backup
from angee.messaging_integrate_whatsapp.backend import confirmed_whatsapp_channel
from angee.workflows_integrate.archives import (
    ArchiveError,
    BoundedReader,
    archive_entries,
    stage_subtree,
)
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor

_RECOGNITION_READ_LIMIT = 128 * 1024 * 1024
"""Maximum bytes one manifest probe may read across ZIP metadata and members."""

_MANIFEST_READ_LIMIT = 64 * 1024 * 1024
"""Maximum uncompressed Manifest.db size accepted during recognition."""


class WhatsAppIphoneBackupExtractor(ArchiveExtractor):
    """Recognize ZIP-wrapped iPhone backups and delegate them to WhatsApp ingest.

    Archive recognition is bounded, not header-only: resolving the store
    requires materializing ``Manifest.db`` (capped at 64MB) per probe before
    the 16-byte store-header check; the store body is never read.
    """

    key = "whatsapp_iphone_backup"
    label = "WhatsApp iPhone backup"
    target_resource = "messaging.Channel"

    def recognizes(self, file: Any) -> bool:
        """Spot-check the manifest-resolved chat store without parsing messages."""

        try:
            with file.open_stream() as stream:
                bounded = BoundedReader(stream, limit=_RECOGNITION_READ_LIMIT)
                with zipfile.ZipFile(cast(BinaryIO, bounded)) as archive:
                    return _recognized_manifest(archive, budget=bounded) is not None
        except (
            ArchiveError,
            backup.BackupError,
            NotImplementedError,
            OSError,
            sqlite3.DatabaseError,
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
        """Import through the existing backup importer into the confirmed channel."""

        channel = confirmed_whatsapp_channel(target_pk)
        total = _import_archive(file, channel=channel, reporter=reporter)
        return {"channel": str(channel.sqid), "imported": total}


def _recognized_manifest(
    archive: zipfile.ZipFile,
    *,
    budget: BoundedReader | None = None,
) -> str | None:
    """Return the archive member for one manifest resolving a WhatsApp store.

    Each manifest probe gets a fresh read budget so an archive bundling many
    device backups cannot exhaust the budget before the recognizable one.
    """

    entries = archive_entries(archive)
    manifests = sorted(name for name in entries if PurePosixPath(name).name == "Manifest.db")
    for manifest_name in manifests:
        if budget is not None:
            budget.remaining = _RECOGNITION_READ_LIMIT
        manifest_info = entries[manifest_name]
        if manifest_info.file_size > _MANIFEST_READ_LIMIT:
            continue
        with tempfile.TemporaryDirectory(prefix="angee-whatsapp-probe-") as temporary:
            root = Path(temporary)
            (root / "Manifest.db").write_bytes(archive.read(manifest_info))
            iphone_backup = backup.IosBackup(root)
            try:
                blob_relative = iphone_backup.blob_relative_path(
                    backup.WHATSAPP_DOMAIN, backup.CHAT_STORAGE_PATH
                )
            finally:
                iphone_backup.close()
        parent = PurePosixPath(manifest_name).parent
        store_name = _joined_member(parent, *blob_relative.parts)
        store_info = entries.get(store_name)
        if store_info is None:
            continue
        with archive.open(store_info) as stream:
            header = stream.read(backup.SQLITE_HEADER_LENGTH)
        if backup.IosBackup.is_chat_storage_header(header):
            return manifest_name
    return None


def _import_archive(
    file: Any,
    *,
    channel: Any,
    reporter: ArchiveExecutionReporter,
) -> int:
    """Safely stage one ZIP-wrapped backup and call the importer owner."""

    try:
        with file.open_stream() as stream, zipfile.ZipFile(stream) as archive:
            manifest_name = _recognized_manifest(archive)
            if manifest_name is None:
                raise backup.BackupError("This archive contains no WhatsApp iPhone backup.")
            parent = PurePosixPath(manifest_name).parent
            with stage_subtree(archive, parent) as backup_root:
                return backup.import_backup(
                    channel,
                    backup_root,
                    on_batch=lambda _total: reporter.heartbeat(),
                )
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise backup.BackupError("This file is not a readable ZIP backup archive.") from error
    except ArchiveError as error:
        raise backup.BackupError(str(error)) from error
    except sqlite3.DatabaseError as error:
        raise backup.BackupError("The WhatsApp chat store in this backup is corrupt or unreadable.") from error


def _joined_member(parent: PurePosixPath, *parts: str) -> str:
    """Join a manifest parent to a content-addressed backup blob path."""

    prefix = () if str(parent) == "." else parent.parts
    return PurePosixPath(*prefix, *parts).as_posix()
