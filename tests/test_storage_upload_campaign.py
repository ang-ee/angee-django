"""Upload isolation, collision cleanup and attachment transaction regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import close_old_connections, connection, connections, transaction
from django.db.utils import OperationalError
from django.utils import timezone
from rebac import actor_context, system_context
from rebac.models import active_relationship_model

from angee.storage import exceptions
from angee.storage import schema as storage_schema
from angee.storage.models import FileAttachmentManager, FileManager, FileVisibility, UploadState
from tests.conftest import (
    Drive,
    File,
    FileAttachment,
    MimeType,
    addon_schema,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
)
from tests.mtidemo.models import MtiChild, MtiParent
from tests.storage_campaign import relationship_storage as relationship_storage
from tests.test_storage import PNG_BYTES, PNG_SHA256, _proxy_upload
from tests.test_storage import drive as drive

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def overwriting_drive(drive: Any, settings: Any) -> Any:
    settings.ANGEE_STORAGE_BACKEND_CLASSES = {
        **settings.ANGEE_STORAGE_BACKEND_CLASSES,
        "local": "tests.storage_campaign.OverwritingBackend",
    }
    return drive


@pytest.mark.parametrize("failure", ["hash_mismatch", "size_mismatch", "too_large"])
def test_failed_upload_never_overwrites_or_deletes_unreadable_legacy_bytes(
    overwriting_drive: Any, tmp_path: Path, settings: Any, failure: str,
) -> None:
    drive = overwriting_drive
    hidden_owner = create_user("legacy-file-owner")
    key = drive.object_key(PNG_SHA256, "same.png")
    with system_context(reason="test.storage.legacy.seed"):
        assert drive.storage.save(key, ContentFile(PNG_BYTES)) == key
        original = File.objects.create(
            drive=drive, owner=hidden_owner, filename="same.png", content_hash=PNG_SHA256,
            storage_path=key, upload_state=UploadState.READY, visibility=FileVisibility.RECORD,
        )
    assert not original.with_actor(drive.alice).has_access("read")
    with actor_context(drive.alice):
        draft = File.objects.draft(
            drive_id=str(drive.sqid), filename="same.png", content_hash=PNG_SHA256,
            size_bytes=len(PNG_BYTES),
        )
        assert draft.pk != original.pk
        assert draft.storage_path != key
        token = draft.issue_upload_token()
        pushing = File.objects.for_upload_token(token)
        if failure == "too_large":
            settings.ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES = len(PNG_BYTES) - 1
            with pytest.raises(exceptions.UploadTooLarge):
                pushing.receive_bytes(BytesIO(PNG_BYTES))
        else:
            payload = b"wrong bytes" if failure == "hash_mismatch" else PNG_BYTES
            pushing.receive_bytes(BytesIO(payload))
            assert (tmp_path / key).read_bytes() == PNG_BYTES
            with pytest.raises(exceptions.UploadConflict):
                pushing.finalize(
                    expected_hash=PNG_SHA256,
                    expected_size=len(PNG_BYTES) + (failure == "size_mismatch"),
                )
        with pytest.raises(exceptions.UploadConflict):
            File.objects.for_upload_token(token)
    with system_context(reason="test.storage.failure.inspect"):
        draft.refresh_from_db()
        original.refresh_from_db()
    assert draft.upload_state == UploadState.FAILED
    assert draft.upload_envelope["failure_reason"] == failure
    assert original.upload_state == UploadState.READY
    assert original.owner_id == hidden_owner.pk
    assert (tmp_path / key).read_bytes() == PNG_BYTES
    assert not (tmp_path / draft.storage_path).exists()


def test_repeated_record_ingest_collisions_leave_no_loser_rows_objects_or_grants(
    overwriting_drive: Any, tmp_path: Path,
) -> None:
    drive = overwriting_drive
    original = _proxy_upload(drive, PNG_BYTES)
    with actor_context(drive.alice):
        original.set_visibility(FileVisibility.RECORD)
        original.delete()
    other = create_user("colliding-ingest-owner")
    with system_context(reason="test.storage.collision.baseline"):
        count = active_relationship_model().objects.count()
    for _ in range(3):
        with pytest.raises(exceptions.UploadConflict, match="^identical bytes already exist$") as raised:
            File.objects.ingest_bytes(
                PNG_BYTES, filename="test.png", drive_id=str(drive.sqid), owner_id=other.pk,
            )
        assert str(original.sqid) not in str(raised.value)
        with system_context(reason="test.storage.collision.inspect"):
            assert list(File.objects.values_list("pk", flat=True)) == [original.pk]
            assert active_relationship_model().objects.count() == count
            original.refresh_from_db()
        assert original.visibility == FileVisibility.RECORD
        assert original.is_trashed
        assert not original.with_actor(other).has_access("read")
        assert {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()} == {
            original.storage_path,
        }
        assert (tmp_path / original.storage_path).read_bytes() == PNG_BYTES


def test_ingest_losing_to_inherited_winner_returns_winner_and_purges_reservation(
    drive: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the precise post-reservation race without depending on a scheduler."""

    original_draft = FileManager.draft
    winner = None

    def reserve_then_publish(manager: Any, **kwargs: Any) -> Any:
        nonlocal winner
        loser = original_draft(manager, **kwargs)
        winner = original_draft(manager, filename="winner.png", drive_id=str(drive.sqid))
        winner.storage.save(winner.storage_path, ContentFile(PNG_BYTES))
        winner.finalize(expected_hash=PNG_SHA256)
        return loser

    monkeypatch.setattr(FileManager, "draft", reserve_then_publish)
    result = File.objects.ingest_bytes(PNG_BYTES, filename="loser.png", drive_id=str(drive.sqid))
    assert winner is not None and result.pk == winner.pk
    with system_context(reason="test.storage.race.inspect"):
        assert list(File.objects.values_list("pk", flat=True)) == [winner.pk]
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()] == [winner.storage_path]


@pytest.mark.parametrize("schema_name", ["public", "console"])
def test_missing_unreadable_unknown_and_untyped_records_have_identical_in_band_denials(
    drive: Any, schema_name: str,
) -> None:
    other = create_user("private-record-owner")
    with system_context(reason="test.storage.denial.seed"):
        hidden = Drive.objects.create(backend=drive.backend, name="Hidden", slug="hidden", owner=other)
    schema = addon_schema(storage_schema.schemas, schema_name)
    records = [
        {"model_label": "storage.Drive", "record_id": str(hidden.sqid)},
        {"model_label": "storage.Drive", "record_id": str(Drive(pk=hidden.pk + 1000).sqid)},
        {"model_label": "absent.Record", "record_id": str(hidden.sqid)},
        {"model_label": "storage.MimeType", "record_id": str(MimeType._base_manager.first().sqid)},
    ]
    payloads = [
        result_data(execute_schema(schema, """
            mutation Begin($input: FileUploadBeginInput!) {
              file_upload_begin(input: $input) {
                error error_code file { id } upload_url upload_token
              }
            }
        """, {"input": {
            "drive": str(drive.sqid), "filename": "denied.png", "visibility": "RECORD", "record": record,
        }}, user=drive.alice))["file_upload_begin"]
        for record in records
    ]
    assert all(payload == payloads[0] for payload in payloads)
    assert payloads[0]["error_code"] == "denied"
    assert payloads[0]["error"]
    assert payloads[0]["file"] is None
    assert not payloads[0]["upload_url"] and not payloads[0]["upload_token"]
    with system_context(reason="test.storage.denial.inspect"):
        assert File.objects.count() == FileAttachment.objects.count() == 0


@pytest.mark.parametrize("record_kind", ["absent", "no_arm", "unwritable"])
def test_refused_record_drafts_create_neither_file_nor_attachment(drive: Any, record_kind: str) -> None:
    record = None
    expected = exceptions.UploadError
    if record_kind == "no_arm":
        record = drive
    elif record_kind == "unwritable":
        with system_context(reason="test.storage.denied.target"):
            record = MtiParent.objects.create(title="Unreadable")
        expected = exceptions.UploadRecordDenied
    with actor_context(drive.alice), pytest.raises(expected):
        File.objects.draft(
            drive_id=str(drive.sqid), filename="record.png", visibility=FileVisibility.RECORD, record=record,
        )
    with system_context(reason="test.storage.record.inspect"):
        assert File.objects.count() == FileAttachment.objects.count() == 0


@pytest.mark.parametrize("failure_at", ["target_lock", "edge_insert"])
def test_target_disappearance_is_denied_but_unrelated_missing_objects_propagate(
    drive: Any, monkeypatch: pytest.MonkeyPatch, failure_at: str,
) -> None:
    error = ObjectDoesNotExist("unrelated edge persistence dependency")

    def disappear(*args: Any, **kwargs: Any) -> None:
        raise error

    method = "_lock_target" if failure_at == "target_lock" else "_attach_authorized"
    monkeypatch.setattr(FileAttachmentManager, method, disappear)
    expected = exceptions.UploadRecordDenied if failure_at == "target_lock" else ObjectDoesNotExist
    with actor_context(drive.alice):
        with pytest.raises(expected) as raised:
            File.objects.draft(filename="atomic.png", drive_id=str(drive.sqid), record=drive)
    if failure_at == "edge_insert":
        assert raised.value is error
    with system_context(reason="test.storage.atomic.inspect"):
        assert File.objects.count() == FileAttachment.objects.count() == 0


def test_dedup_attachment_rolls_back_when_restore_fails(drive: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    with actor_context(drive.alice):
        row.delete()

    def fail_restore(self: Any) -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(File, "restore", fail_restore)
    with actor_context(drive.alice), pytest.raises(RuntimeError, match="restore failed"):
        File.objects.draft(
            drive_id=str(drive.sqid), filename="again.png", content_hash=PNG_SHA256, record=drive,
        )
    with system_context(reason="test.storage.rollback.inspect"):
        assert FileAttachment.objects.count() == 0
        assert File.objects.count() == 1
        row.refresh_from_db()
    assert row.is_trashed


def test_prune_cascades_failed_and_abandoned_edges_without_deleting_surviving_bytes(
    drive: Any, tmp_path: Path, settings: Any,
) -> None:
    survivor = _proxy_upload(drive, PNG_BYTES)
    old = timezone.now() - timedelta(hours=settings.ANGEE_STORAGE_DRAFT_TTL_HOURS + 1)
    with actor_context(drive.alice):
        abandoned = File.objects.draft(filename="abandoned.png", drive_id=str(drive.sqid), record=drive)
        failed = File.objects.draft(filename="failed.png", drive_id=str(drive.sqid), record=drive)
        recent = File.objects.draft(filename="recent.png", drive_id=str(drive.sqid), record=drive)
    with system_context(reason="test.storage.prune.seed"):
        File.objects.filter(pk=failed.pk).update(upload_state=UploadState.FAILED)
        File.objects.filter(pk__in=[abandoned.pk, failed.pk]).update(
            created_at=old, storage_path=survivor.storage_path,
        )
    output = StringIO()
    call_command("storage_prune", stdout=output)
    assert "purged=2" in output.getvalue()
    with system_context(reason="test.storage.prune.inspect"):
        assert set(File.objects.values_list("pk", flat=True)) == {survivor.pk, recent.pk}
        assert list(FileAttachment.objects.values_list("file_id", flat=True)) == [recent.pk]
    assert (tmp_path / survivor.storage_path).read_bytes() == PNG_BYTES


@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL canonical upload-target locking contract")
def test_draft_locks_canonical_mti_target_before_inserting_file(drive: Any) -> None:
    admin = create_platform_admin("target-lock-admin")
    with system_context(reason="test.storage.lock.seed"):
        child = MtiChild.objects.create(title="Target", detail="Child")

    def draft_while_locked() -> str:
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '250ms'")
            with actor_context(admin):
                try:
                    File.objects.draft(filename="locked.png", drive_id=str(drive.sqid), record=child)
                except OperationalError as error:
                    return str(error)
            raise AssertionError("Draft did not wait for the canonical record lock")
        finally:
            connections.close_all()

    with system_context(reason="test.storage.lock.hold"), transaction.atomic():
        MtiParent._base_manager.select_for_update().get(pk=child.pk)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert "lock timeout" in pool.submit(draft_while_locked).result(timeout=5).lower()
        assert File.objects.count() == FileAttachment.objects.count() == 0
    with actor_context(admin):
        row = File.objects.draft(filename="unlocked.png", drive_id=str(drive.sqid), record=child)
    with system_context(reason="test.storage.lock.inspect"):
        edge = FileAttachment.objects.get(file=row)
    assert edge.content_type.model_class() is MtiParent
    assert edge.object_id == child.pk


@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL two-writer ingest collision contract")
def test_two_ingest_writers_converge_without_losing_rows_or_objects(
    drive: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(2, timeout=5)
    original_finalize = File.finalize

    def finalize_together(self: Any, **kwargs: Any) -> Any:
        barrier.wait()
        return original_finalize(self, **kwargs)

    monkeypatch.setattr(File, "finalize", finalize_together)

    def ingest(filename: str) -> Any:
        close_old_connections()
        try:
            with actor_context(drive.alice):
                return File.objects.ingest_bytes(
                    PNG_BYTES, filename=filename, drive_id=str(drive.sqid), owner_id=drive.alice.pk,
                ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ingest, ("first.png", "second.png")))
    assert results[0] == results[1]
    with system_context(reason="test.storage.two-writers.inspect"):
        row = File.objects.get()
    assert row.pk == results[0]
    assert row.upload_state == UploadState.READY
    assert {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()} == {row.storage_path}
    assert (tmp_path / row.storage_path).read_bytes() == PNG_BYTES
