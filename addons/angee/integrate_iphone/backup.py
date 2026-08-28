"""Read-only access to one unencrypted Finder/iTunes iPhone backup."""

from __future__ import annotations

import hashlib
import plistlib
import sqlite3
import string
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_HEADER_LENGTH = len(SQLITE_HEADER)
"""Bytes needed to recognize a SQLite database without reading its body."""


class BackupError(Exception):
    """The backup directory is missing, encrypted, corrupt, or unreadable."""


@dataclass(frozen=True)
class ManifestFile:
    """One regular file resolved from ``Manifest.db`` to its physical blob."""

    domain: str
    relative_path: str
    file_id: str
    blob_path: Path


class IosBackup:
    """Read-only view over one unencrypted iPhone backup directory."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        manifest_db = self.path / "Manifest.db"
        if not manifest_db.is_file():
            raise BackupError(f"{self.path} is not an iPhone backup (no Manifest.db).")
        self._reject_encrypted()
        try:
            self._manifest = sqlite3.connect(f"file:{manifest_db}?mode=ro", uri=True)
            self._manifest.execute("SELECT fileID, domain, relativePath FROM Files LIMIT 0")
        except sqlite3.DatabaseError as error:
            connection = getattr(self, "_manifest", None)
            if connection is not None:
                connection.close()
            raise BackupError(f"{manifest_db} is not a readable iPhone backup manifest.") from error

    def _reject_encrypted(self) -> None:
        """Fail loudly on an encrypted backup — decryption is out of scope."""

        manifest_plist = self.path / "Manifest.plist"
        if not manifest_plist.exists():
            return
        try:
            manifest = plistlib.loads(manifest_plist.read_bytes())
        except (OSError, plistlib.InvalidFileException) as error:
            raise BackupError(f"{manifest_plist} could not be parsed.") from error
        if manifest.get("IsEncrypted"):
            raise BackupError(
                "This iPhone backup is encrypted. Create an unencrypted backup "
                "(Finder → uncheck 'Encrypt local backup') and import that."
            )

    def blob_path(self, domain: str, relative_path: str) -> Path | None:
        """Return the on-disk blob for one backed-up file, or ``None``."""

        blob = self.path / self.blob_relative_path(domain, relative_path)
        return blob if blob.is_file() else None

    def blob_relative_path(self, domain: str, relative_path: str) -> PurePosixPath:
        """Return the backup-relative ``<fileID[:2]>/<fileID>`` blob location."""

        file_id = self.blob_id(domain, relative_path)
        if len(file_id) != 40 or any(character not in string.hexdigits for character in file_id):
            raise BackupError(
                f"Manifest file id for {domain}/{relative_path} is not a SHA1 digest."
            )
        return PurePosixPath(file_id[:2], file_id)

    def blob_id(self, domain: str, relative_path: str) -> str:
        """Return one file's manifest id or its deterministic fallback id."""

        try:
            row = self._manifest.execute(
                "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?",
                (domain, relative_path),
            ).fetchone()
        except sqlite3.DatabaseError as error:
            raise BackupError("The iPhone backup manifest could not be read.") from error
        return str(row[0]) if row else hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()

    def iter_files(self) -> Iterator[ManifestFile]:
        """Yield valid physical files in deterministic logical-path order.

        Older manifests without the ``flags`` column cannot distinguish files
        from directories, so every row is treated as a file and missing blobs
        are still skipped. Malformed rows are contained to that row.
        """

        try:
            cursor = self._manifest.execute(
                "SELECT fileID, domain, relativePath FROM Files "
                "WHERE flags = 1 ORDER BY domain, relativePath, fileID"
            )
        except sqlite3.OperationalError as error:
            if "no such column: flags" not in str(error):
                raise BackupError("The iPhone backup manifest could not be enumerated.") from error
            cursor = self._manifest.execute(
                "SELECT fileID, domain, relativePath FROM Files "
                "ORDER BY domain, relativePath, fileID"
            )
        for raw_file_id, raw_domain, raw_relative_path in cursor:
            file_id = str(raw_file_id)
            if len(file_id) != 40 or any(character not in string.hexdigits for character in file_id):
                continue
            blob = self.path / file_id[:2] / file_id
            if not blob.is_file():
                continue
            yield ManifestFile(
                domain=str(raw_domain or ""),
                relative_path=str(raw_relative_path or ""),
                file_id=file_id,
                blob_path=blob,
            )

    def iter_dirs(self) -> Iterator[tuple[str, str]]:
        """Yield manifest directories as ``(domain, relative_path)`` pairs."""

        try:
            cursor = self._manifest.execute(
                "SELECT domain, relativePath FROM Files "
                "WHERE flags = 2 ORDER BY domain, relativePath"
            )
        except sqlite3.OperationalError as error:
            if "no such column: flags" in str(error):
                return
            raise BackupError("The iPhone backup manifest could not be enumerated.") from error
        for raw_domain, raw_relative_path in cursor:
            yield str(raw_domain or ""), str(raw_relative_path or "")

    def read(self, domain: str, relative_path: str) -> bytes | None:
        """Return one backed-up file's bytes, or ``None`` when absent."""

        blob = self.blob_path(domain, relative_path)
        return blob.read_bytes() if blob is not None else None

    def close(self) -> None:
        """Close the read-only manifest connection."""

        self._manifest.close()


def logical_key(domain: str, relative_path: str) -> str:
    """Return the canonical ``domain/relativePath`` key for one backup entry."""

    raw_domain = str(domain)
    raw_relative_path = str(relative_path)
    key = f"{raw_domain}/{raw_relative_path}"
    parsed_domain, parsed_relative_path = split_logical_key(key)
    if parsed_domain != raw_domain or parsed_relative_path != raw_relative_path:
        raise BackupError(f"Invalid iPhone backup logical key: {key!r}")
    return key


def split_logical_key(key: str) -> tuple[str, str]:
    """Validate and split one canonical ``domain/relativePath`` key."""

    raw_key = str(key)
    logical = PurePosixPath(raw_key)
    if (
        logical.is_absolute()
        or len(logical.parts) < 2
        or ".." in logical.parts
        or "\\" in raw_key
        or logical.as_posix() != raw_key
    ):
        raise BackupError(f"Invalid iPhone backup logical key: {raw_key!r}")
    return logical.parts[0], PurePosixPath(*logical.parts[1:]).as_posix()


def is_sqlite_header(value: bytes) -> bool:
    """Return whether ``value`` begins with SQLite's fixed file header."""

    return value.startswith(SQLITE_HEADER)
