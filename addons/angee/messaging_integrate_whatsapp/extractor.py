"""WhatsApp iPhone-backup client for the archive workflow bridge."""

from __future__ import annotations

import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.messaging_integrate_whatsapp import backup
from angee.messaging_integrate_whatsapp.backend import WhatsAppChannelBackend
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor

_RECOGNITION_READ_LIMIT = 128 * 1024 * 1024
"""Maximum bytes one manifest probe may read across ZIP metadata and members."""

_MANIFEST_READ_LIMIT = 64 * 1024 * 1024
"""Maximum uncompressed Manifest.db size accepted during recognition."""

_EXTRACT_DECLARED_LIMIT = 128 * 1024 * 1024 * 1024
"""Aggregate declared uncompressed bytes accepted at execute-time extraction."""


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
                bounded = _BoundedReader(stream, limit=_RECOGNITION_READ_LIMIT)
                with zipfile.ZipFile(cast(BinaryIO, bounded)) as archive:
                    return _recognized_manifest(archive, budget=bounded) is not None
        except (
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

        channel = _confirmed_channel(target_pk)
        total = _import_archive(file, channel=channel, reporter=reporter)
        return {"channel": str(channel.sqid), "imported": total}


class _BoundedReader:
    """Seekable binary-stream proxy enforcing one aggregate recognition budget."""

    def __init__(self, stream: BinaryIO, *, limit: int) -> None:
        self.stream = stream
        self.remaining = limit

    def read(self, size: int = -1) -> bytes:
        """Read at most the remaining budget, rejecting unbounded requests."""

        if size < 0 or size > self.remaining:
            raise backup.BackupError("WhatsApp backup recognition exceeded its bounded read budget.")
        value = self.stream.read(size)
        self.remaining -= len(value)
        return value

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek without spending the byte-read budget."""

        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        """Return the wrapped stream position."""

        return self.stream.tell()

    def seekable(self) -> bool:
        """Return whether the wrapped storage stream supports ZIP random access."""

        return self.stream.seekable()


def _recognized_manifest(
    archive: zipfile.ZipFile,
    *,
    budget: _BoundedReader | None = None,
) -> str | None:
    """Return the archive member for one manifest resolving a WhatsApp store.

    Each manifest probe gets a fresh read budget so an archive bundling many
    device backups cannot exhaust the budget before the recognizable one.
    """

    entries = _archive_entries(archive)
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
            entries = _subtree_entries(_archive_entries(archive), parent)
            declared = sum(info.file_size for info in entries.values())
            if declared > _EXTRACT_DECLARED_LIMIT:
                raise backup.BackupError("Backup archive exceeds the supported extraction size.")
            with tempfile.TemporaryDirectory(prefix="angee-whatsapp-import-") as temporary:
                root = Path(temporary)
                _extract_archive(archive, root, entries=entries)
                backup_root = root.joinpath(*parent.parts)
                return backup.import_backup(
                    channel,
                    backup_root,
                    on_batch=lambda _total: reporter.heartbeat(),
                )
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise backup.BackupError("This file is not a readable ZIP backup archive.") from error
    except sqlite3.DatabaseError as error:
        raise backup.BackupError("The WhatsApp chat store in this backup is corrupt or unreadable.") from error


def _archive_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Return safe, unique archive members keyed by normalized POSIX path."""

    entries: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        name = _safe_member_name(info.filename)
        if name in entries:
            raise backup.BackupError(f"Backup archive repeats member {name!r}.")
        entries[name] = info
    return entries


def _safe_member_name(value: str) -> str:
    """Return a normalized relative member path or reject traversal/symlinks."""

    if "\\" in value:
        raise backup.BackupError(f"Backup archive member {value!r} is not a POSIX path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise backup.BackupError(f"Backup archive member {value!r} escapes its archive root.")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts:
        raise backup.BackupError("Backup archive contains an empty member path.")
    return PurePosixPath(*parts).as_posix()


def _joined_member(parent: PurePosixPath, *parts: str) -> str:
    """Join a manifest parent to a content-addressed backup blob path."""

    prefix = () if str(parent) == "." else parent.parts
    return PurePosixPath(*prefix, *parts).as_posix()


def _subtree_entries(
    entries: dict[str, zipfile.ZipInfo],
    parent: PurePosixPath,
) -> dict[str, zipfile.ZipInfo]:
    """Return only the members inside ``parent`` — the backup the importer reads."""

    if str(parent) == ".":
        return entries
    prefix = parent.as_posix() + "/"
    return {name: info for name, info in entries.items() if name.startswith(prefix)}


def _extract_archive(
    archive: zipfile.ZipFile,
    root: Path,
    *,
    entries: dict[str, zipfile.ZipInfo],
) -> None:
    """Extract normalized regular files and directories without following links."""

    for name, info in sorted(entries.items()):
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise backup.BackupError(f"Backup archive member {name!r} is a symbolic link.")
        target = root.joinpath(*PurePosixPath(name).parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


def _confirmed_channel(target_pk: str) -> Any:
    """Return the confirmed WhatsApp channel named by its public sqid."""

    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_whatsapp.archive_import.channel"):
        channel = channel_model._base_manager.filter(
            sqid=target_pk,
            backend_class=WhatsAppChannelBackend.key,
        ).first()
    if channel is None:
        raise ValidationError({"target": f"No WhatsApp channel {target_pk!r}."})
    return channel
