"""Native file acquisition, retained-carrier cleanup and source-reader lifetimes."""

import sqlite3
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from rebac import system_context

from angee.extraction.acquisition import PartCarrier
from angee.extraction.managers import ExtractionManager
from angee.integrate_iphone.backup import SQLITE_HEADER, SQLITE_HEADER_LENGTH, IosBackup
from angee.storage.managers import FileManager
from angee.storage.models import File as AbstractFile
from angee.storage_integrate_iphone.mounts import IphoneBackupMountBackend
from tests.conftest import File, Folder, Mount
from tests.test_extraction_models import evidence as evidence
from tests.test_integrate_iphone import CAMERA_DOMAIN, CAMERA_PATH, build_iphone_backup
from tests.test_storage import drive as drive


def test_ingest_creation_status_distinguishes_a_shared_winner(drive):
    with system_context(reason="test native ingestion receipt"):
        first, created = File.objects.get_or_ingest_bytes(b"receipt", filename="receipt.txt", drive_id=str(drive.sqid))
        winner, created_again = File.objects.get_or_ingest_bytes(
            b"receipt", filename="other.txt", drive_id=str(drive.sqid),
        )
        assert created and not created_again and winner.pk == first.pk


def test_metadata_merge_preserves_other_stale_writers(drive):
    with system_context(reason="test metadata owner lock"):
        file = File.objects.ingest_bytes(b"metadata", filename="metadata.txt", drive_id=str(drive.sqid))
        stale = File.objects.get(pk=file.pk)
        file.merge_metadata({"first": {"a": 1}})
        stale.merge_metadata({"second": {"b": 2}})
        file.merge_metadata({"first": {"c": 3}})
        file.refresh_from_db()
        assert file.metadata == {"first": {"a": 1, "c": 3}, "second": {"b": 2}}


def test_carrier_storage_io_precedes_lineage_atomic(evidence, monkeypatch):
    retain, _values = evidence
    ingest, read = FileManager.get_or_ingest_bytes, File.read_verified
    calls = []

    def ingest_outside(self, *args, **kwargs):
        assert not connection.in_atomic_block
        calls.append("write")
        return ingest(self, *args, **kwargs)

    def read_outside(self, *args, **kwargs):
        assert not connection.in_atomic_block
        calls.append("read")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(FileManager, "get_or_ingest_bytes", ingest_outside)
    monkeypatch.setattr(File, "read_verified", read_outside)
    row = retain()
    assert calls == ["write", "read"]
    assert row.parts.count() == 1


def test_failed_allocation_discards_only_its_new_unretained_carrier(evidence, monkeypatch):
    retain, _values = evidence
    with system_context(reason="test carrier inventory"):
        before = set(File.objects.values_list("pk", flat=True))

    def refuse(*args, **kwargs):
        raise ValidationError("allocation refused")

    monkeypatch.setattr(ExtractionManager, "_retain_prepared", refuse)
    with pytest.raises(ValidationError, match="allocation refused"):
        retain()
    with system_context(reason="test carrier cleanup"):
        assert set(File.objects.values_list("pk", flat=True)) == before


def test_cleanup_preserves_a_carrier_committed_by_another_retainer(evidence, monkeypatch):
    retain, _values = evidence
    original = ExtractionManager._retain_prepared
    retained = []

    def retain_then_refuse(self, *args, **kwargs):
        retained.append(original(self, *args, **kwargs))
        raise ValidationError("the losing caller refused")

    monkeypatch.setattr(ExtractionManager, "_retain_prepared", retain_then_refuse)
    with pytest.raises(ValidationError, match="losing caller"):
        retain()
    with system_context(reason="test shared carrier survives cleanup"):
        carrier = retained[0].parts.get().carrier_file
        assert File.objects.filter(pk=carrier.pk).exists()
        assert not carrier.purge_if_unretained()


def test_sqlite_probe_reads_only_the_fixed_header(tmp_path, monkeypatch):
    root = build_iphone_backup(tmp_path / "backup")
    backup = IosBackup(root)
    try:
        blob = backup.blob_path(CAMERA_DOMAIN, CAMERA_PATH)
        blob.write_bytes(SQLITE_HEADER + b"x" * 100_000)
        original = Path.open
        reads = []

        class Probe:
            def __enter__(self):
                self.stream = original(blob, "rb")
                return self

            def read(self, count):
                reads.append(count)
                return self.stream.read(count)

            def __exit__(self, *args):
                self.stream.close()

        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: Probe())
        assert backup.has_sqlite_file(CAMERA_DOMAIN, CAMERA_PATH)
        assert reads == [SQLITE_HEADER_LENGTH]
    finally:
        backup.close()


def test_iphone_backend_close_releases_the_cached_manifest(tmp_path, settings):
    root = build_iphone_backup(tmp_path / "backup")
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.ANGEE_DATA_DIR = tmp_path / "data"
    backend = IphoneBackupMountBackend(Mount(config={"root": str(root)}))
    reader = backend.backup
    backend._blob_by_path["cached"] = root
    backend.close()
    assert backend._backup_reader is None and backend._blob_by_path == {}
    with pytest.raises(sqlite3.ProgrammingError):
        reader._manifest.execute("SELECT 1")
    backend.close()


def test_source_identity_is_rechecked_after_carrier_preparation(evidence, monkeypatch):
    retain, values = evidence
    read = PartCarrier.read

    def mutate_source(self, file):
        part = read(self, file)
        source = values["target"]
        source.content_hash = "b" * 64
        source.save(update_fields=["content_hash"])
        return part

    monkeypatch.setattr(PartCarrier, "read", mutate_source)
    with pytest.raises(ValidationError, match="source changed"):
        retain()


def test_created_receipt_does_not_authorize_removal_after_dedup_sharing(drive, monkeypatch):
    monkeypatch.setattr(File, "rebac_grantable", AbstractFile.rebac_grantable)
    with system_context(reason="test shared dedup winner retention"):
        other = get_user_model().objects.create_user(username="other-ingest-owner")
        original, created = File.objects.get_or_ingest_bytes(
            b"shared receipt", filename="shared.txt", drive_id=str(drive.sqid), owner_id=drive.alice.pk,
        )
        shared, another_created = File.objects.get_or_ingest_bytes(
            b"shared receipt", filename="same.txt", drive_id=str(drive.sqid), owner_id=other.pk,
        )
        assert created and not another_created and shared.pk == original.pk
        assert not original.purge_if_unretained()
        assert File.objects.filter(pk=shared.pk).exists()


def test_cleanup_preserves_committed_edits_after_creation(drive):
    with system_context(reason="test modified acquisition retention"):
        original, created = File.objects.get_or_ingest_bytes(
            b"edited receipt", filename="edited.txt", drive_id=str(drive.sqid),
        )
        current = File.objects.get(pk=original.pk)
        current.title = "Kept by another caller"
        current.save(update_fields=["title", "updated_at"])
        assert created and not original.purge_if_unretained()
        assert File.objects.filter(pk=current.pk).exists()


def test_scoped_folder_prune_uses_native_ancestry_without_widening_deletion(drive):
    with system_context(reason="test scoped folder inventory"):
        parent = Folder.objects.ensure_path(drive, ("parent",))
        child = Folder.objects.ensure_path(drive, ("parent", "child"))
        assert Folder.objects.filter(pk=parent.pk).prune_missing(drive, ()) == 0
        assert Folder.objects.filter(pk=child.pk).prune_missing(drive, (("parent", "child"),)) == 0
        assert Folder.objects.filter(pk=child.pk).prune_missing(drive, ()) == 1
        assert Folder.objects.filter(pk=parent.pk).exists()


def test_folder_prune_retains_a_trashed_files_parent(drive):
    with system_context(reason="test retained folder protection"):
        folder = Folder.objects.ensure_path(drive, ("retained",))
        file = File.objects.ingest_bytes(
            b"retained file", filename="retained.txt", drive_id=str(drive.sqid), folder_id=str(folder.sqid),
        )
        file.delete()
        assert Folder.objects.prune_missing(drive, ()) == 0
        file.refresh_from_db()
        assert file.is_trashed and file.folder_id == folder.pk
