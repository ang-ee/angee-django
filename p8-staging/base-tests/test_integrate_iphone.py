"""Tests for the dependency-free Apple-device backup reader."""

from __future__ import annotations

import os
import plistlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from angee.integrate_iphone.backup import BackupError, IosBackup

BASE_MTIME_NS = 1_700_000_000_000_000_000
CAMERA_DOMAIN = "CameraRollDomain"
CAMERA_PATH = "Media/DCIM/100APPLE/IMG_0001.JPG"
NOTES_DOMAIN = "HomeDomain"
NOTES_PATH = "Library/Notes/note.txt"
CAMERA_BYTES = b"\xff\xd8\xffiphone-photo"
NOTES_BYTES = b"backup note"


def build_iphone_backup(
    root: Path,
    *,
    encrypted: bool = False,
    flags: bool = True,
    malformed_file_id: bool = False,
) -> Path:
    """Build a deterministic manifest with files, directories, and bad rows."""

    root.mkdir(parents=True)
    (root / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": encrypted}))
    manifest = sqlite3.connect(root / "Manifest.db")
    flag_column = ", flags INTEGER" if flags else ""
    manifest.execute(
        f"CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT{flag_column})"
    )

    def insert(file_id: str, domain: str, relative_path: str, flag: int) -> None:
        values: tuple[Any, ...]
        if flags:
            values = (file_id, domain, relative_path, flag)
            manifest.execute("INSERT INTO Files VALUES (?, ?, ?, ?)", values)
        else:
            values = (file_id, domain, relative_path)
            manifest.execute("INSERT INTO Files VALUES (?, ?, ?)", values)

    if flags:
        for index, (domain, relative_path) in enumerate(
            (
                (CAMERA_DOMAIN, ""),
                (CAMERA_DOMAIN, "Media"),
                (CAMERA_DOMAIN, "Media/DCIM"),
                (CAMERA_DOMAIN, "Media/DCIM/100APPLE"),
                (NOTES_DOMAIN, ""),
                (NOTES_DOMAIN, "Library"),
                (NOTES_DOMAIN, "Library/Notes"),
            ),
            start=1,
        ):
            insert(f"dir-{index}", domain, relative_path, 2)

    files = (
        ("10" * 20, CAMERA_DOMAIN, CAMERA_PATH, CAMERA_BYTES, BASE_MTIME_NS),
        ("20" * 20, NOTES_DOMAIN, NOTES_PATH, NOTES_BYTES, BASE_MTIME_NS + 1),
    )
    for file_id, domain, relative_path, content, mtime_ns in files:
        insert(file_id, domain, relative_path, 1)
        blob = root / file_id[:2] / file_id
        blob.parent.mkdir(exist_ok=True)
        blob.write_bytes(content)
        os.utime(blob, ns=(mtime_ns, mtime_ns))

    if flags:
        symlink_id = "30" * 20
        insert(symlink_id, CAMERA_DOMAIN, "Media/skipped-link", 4)
        symlink_blob = root / symlink_id[:2] / symlink_id
        symlink_blob.parent.mkdir(exist_ok=True)
        symlink_blob.write_bytes(b"not a regular manifest file")
        insert("40" * 20, CAMERA_DOMAIN, "Media/missing.jpg", 1)
        if malformed_file_id:
            insert("not-a-sha1", CAMERA_DOMAIN, "Media/malformed.jpg", 1)

    manifest.commit()
    manifest.close()
    return root


def test_iter_files_uses_one_query_and_skips_a_malformed_file_id(tmp_path: Path) -> None:
    """N manifest rows require one SELECT and a bad row cannot abort enumeration."""

    backup = IosBackup(
        build_iphone_backup(tmp_path / "device", malformed_file_id=True)
    )
    queries: list[str] = []
    backup._manifest.set_trace_callback(queries.append)
    try:
        files = list(backup.iter_files())
    finally:
        backup.close()

    assert [(item.domain, item.relative_path, item.file_id) for item in files] == [
        (CAMERA_DOMAIN, CAMERA_PATH, "10" * 20),
        (NOTES_DOMAIN, NOTES_PATH, "20" * 20),
    ]
    assert [item.blob_path.read_bytes() for item in files] == [CAMERA_BYTES, NOTES_BYTES]
    assert len([query for query in queries if query.lstrip().upper().startswith("SELECT")]) == 1


def test_reader_maps_real_directories_and_skips_missing_or_link_rows(tmp_path: Path) -> None:
    """Flags retain the real folder tree while absent files and links stay excluded."""

    backup = IosBackup(build_iphone_backup(tmp_path / "device"))
    try:
        files = list(backup.iter_files())
        directories = list(backup.iter_dirs())
    finally:
        backup.close()

    assert [item.relative_path for item in files] == [CAMERA_PATH, NOTES_PATH]
    assert directories == [
        (CAMERA_DOMAIN, ""),
        (CAMERA_DOMAIN, "Media"),
        (CAMERA_DOMAIN, "Media/DCIM"),
        (CAMERA_DOMAIN, "Media/DCIM/100APPLE"),
        (NOTES_DOMAIN, ""),
        (NOTES_DOMAIN, "Library"),
        (NOTES_DOMAIN, "Library/Notes"),
    ]


def test_reader_degrades_gracefully_without_manifest_flags(tmp_path: Path) -> None:
    """Legacy manifests enumerate existing file blobs and report no explicit dirs."""

    backup = IosBackup(build_iphone_backup(tmp_path / "legacy", flags=False))
    try:
        assert [item.relative_path for item in backup.iter_files()] == [
            CAMERA_PATH,
            NOTES_PATH,
        ]
        assert list(backup.iter_dirs()) == []
    finally:
        backup.close()


def test_reader_rejects_encrypted_backup(tmp_path: Path) -> None:
    """Encrypted Finder backups fail before their manifest is opened."""

    root = build_iphone_backup(tmp_path / "encrypted", encrypted=True)
    with pytest.raises(BackupError, match="encrypted"):
        IosBackup(root)
