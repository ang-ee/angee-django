"""Tests for iMessage's iPhone-backup workflow extractor client."""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps

from angee.addons import addon_contract
from angee.messaging_integrate_imessage import extractor as extractor_module
from angee.messaging_integrate_imessage.autoconfig import SETTINGS as IMESSAGE_SETTINGS
from angee.messaging_integrate_imessage.extractor import ImessageIphoneBackupExtractor
from angee.messaging_integrate_imessage.store import SMS_DOMAIN, SMS_PATH


class _BackupArchiveFile:
    """Minimal storage.File-shaped object opening one ZIP-wrapped backup."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def open_stream(self) -> Any:
        """Open the stored backup bytes for extractor recognition/execution."""

        return self.path.open("rb")


def test_imessage_iphone_backup_recognizes_manifest_resolved_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recognition reads the manifest and the store header — never the store body."""

    consumed: list[int] = []

    class RecordingReader(extractor_module.BoundedReader):
        def read(self, size: int = -1) -> bytes:
            value = super().read(size)
            consumed.append(len(value))
            return value

    monkeypatch.setattr(extractor_module, "BoundedReader", RecordingReader)
    store_padding = 1024 * 1024
    archive = _minimal_backup_archive(tmp_path, store_padding=store_padding)

    assert ImessageIphoneBackupExtractor().recognizes(_BackupArchiveFile(archive)) is True
    # ZIP metadata + the small fixture manifest + the 16-byte header stay far
    # below the padded store body a full read would consume.
    assert sum(consumed) < store_padding // 4


def test_imessage_iphone_backup_rejects_foreign_archives(tmp_path: Path) -> None:
    """Recognition returns False for near-miss layouts instead of over-claiming."""

    extractor = ImessageIphoneBackupExtractor()

    storeless = _minimal_backup_archive(tmp_path / "storeless", include_store=False)
    assert extractor.recognizes(_BackupArchiveFile(storeless)) is False

    manifestless_dir = tmp_path / "manifestless"
    manifestless_dir.mkdir()
    manifestless = manifestless_dir / "plain.zip"
    with zipfile.ZipFile(manifestless, "w") as archive:
        archive.writestr("device/notes.txt", "not a backup")
    assert extractor.recognizes(_BackupArchiveFile(manifestless)) is False

    random_dir = tmp_path / "random"
    random_dir.mkdir()
    random_bytes = random_dir / "noise.bin"
    random_bytes.write_bytes(b"\x00\x01definitely not a zip archive\xff" * 64)
    assert extractor.recognizes(_BackupArchiveFile(random_bytes)) is False


@pytest.mark.django_db
def test_imessage_iphone_backup_execute_delegates_to_backup_importer_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execution resolves the confirmed iMessage channel and calls the importer facade."""

    target = "int_confirmed"
    channel = SimpleNamespace(sqid=target)
    resolved: list[str] = []
    monkeypatch.setattr(
        extractor_module,
        "confirmed_imessage_channel",
        lambda sqid: resolved.append(sqid) or channel,
    )
    calls: list[dict[str, Any]] = []

    def import_backup(resolved_channel: Any, backup_dir: Path, *, on_batch: Any) -> int:
        calls.append(
            {"channel": resolved_channel, "manifest": (backup_dir / "Manifest.db").is_file()}
        )
        on_batch(4)
        return 4

    monkeypatch.setattr(extractor_module, "import_backup", import_backup)
    heartbeats: list[bool] = []
    reporter = SimpleNamespace(heartbeat=lambda: heartbeats.append(True))
    archive = _minimal_backup_archive(tmp_path)

    result = ImessageIphoneBackupExtractor().execute(_BackupArchiveFile(archive), target, reporter)

    assert resolved == [target]
    assert calls == [{"channel": channel, "manifest": True}]
    assert heartbeats == [True]
    assert result == {"channel": target, "imported": 4}


def test_imessage_addon_registers_extractor_and_depends_on_bridge() -> None:
    """iMessage contributes its vendor extractor through the bridge's settings seam."""

    config = apps.get_app_config("messaging_integrate_imessage")
    contract = addon_contract(config)

    assert contract is not None
    assert "angee.workflows_integrate" in contract.depends_on
    assert IMESSAGE_SETTINGS[
        "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.imessage_iphone_backup"
    ] == "angee.messaging_integrate_imessage.extractor.ImessageIphoneBackupExtractor"


def _minimal_backup_layout(
    tmp_path: Path,
    *,
    include_store: bool = True,
    store_padding: int = 0,
) -> Path:
    """Build the smallest Manifest.db plus sms.db backup fixture."""

    backup_root = tmp_path / "backup"
    backup_root.mkdir(parents=True)
    file_id = "ab" * 20
    manifest = sqlite3.connect(backup_root / "Manifest.db")
    manifest.execute("CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT)")
    manifest.execute("INSERT INTO Files VALUES (?, ?, ?)", (file_id, SMS_DOMAIN, SMS_PATH))
    manifest.commit()
    manifest.close()

    if include_store:
        sms_store = tmp_path / "sms.db"
        store = sqlite3.connect(sms_store)
        store.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY)")
        store.commit()
        store.close()
        blob = backup_root / file_id[:2] / file_id
        blob.parent.mkdir()
        content = sms_store.read_bytes()
        if store_padding:
            content += b"\x00" * store_padding
        blob.write_bytes(content)
    assert file_id != hashlib.sha1(f"{SMS_DOMAIN}-{SMS_PATH}".encode()).hexdigest()
    return backup_root


def _minimal_backup_archive(
    tmp_path: Path,
    *,
    include_store: bool = True,
    store_padding: int = 0,
) -> Path:
    """Wrap the minimal fixture layout in the storage.File archive shape."""

    backup_root = _minimal_backup_layout(
        tmp_path, include_store=include_store, store_padding=store_padding
    )
    archive_path = tmp_path / "iphone-backup.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(backup_root.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=(Path("device") / path.relative_to(backup_root)).as_posix())
    return archive_path
