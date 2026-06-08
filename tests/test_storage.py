"""Tests for the storage upload protocol and file lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from rebac import actor_context, system_context

from angee.storage import exceptions
from angee.storage.models import FileManager, UploadState
from tests.conftest import (
    STORAGE_TEST_MODELS,
    Backend,
    Drive,
    File,
    Folder,
    MimeType,
    _create_missing_tables,
)

# A real 1x1 PNG — libmagic classifies it as image/png; a fake signature would not.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)
PNG_SHA256 = hashlib.sha256(PNG_BYTES).hexdigest()


def test_file_source_model_owns_the_upload_protocol() -> None:
    """The factory lives on the manager; byte intake and publish on the row."""

    from angee.storage.models import File as AbstractFile

    assert AbstractFile._meta.abstract is True
    assert AbstractFile.is_runtime_model() is True
    assert isinstance(File.objects, FileManager)
    assert hasattr(File.objects, "draft")
    assert all(hasattr(AbstractFile, verb) for verb in ("receive_bytes", "finalize", "issue_upload_token"))
    assert set(UploadState.values) == {"draft", "ready", "failed"}


@pytest.fixture
def storage_tables() -> Iterator[None]:
    """Provide the concrete storage tables for one test."""

    created_models = _create_missing_tables(STORAGE_TEST_MODELS)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.fixture
def drive(tmp_path: Path, storage_tables: None) -> Any:
    """Provide a local-backend drive owned by the ``alice`` test user."""

    del storage_tables
    call_command("rebac", "sync", verbosity=0)
    alice = get_user_model().objects.create_user(username="storage-alice", email="alice@example.com")
    with system_context(reason="test storage setup"):
        backend = Backend._base_manager.create(
            slug="local",
            label="Local",
            backend_class="angee.storage.backends.LocalBackend",
            backend_config={"root": str(tmp_path), "base_url": "/media/"},
        )
        MimeType._base_manager.create(mime_type="image/png", category="image", label="PNG image")
        MimeType._base_manager.create(mime_type="application/octet-stream", category="other", label="Binary file")
        row = Drive._base_manager.create(
            backend=backend,
            slug="assets",
            name="Assets",
            prefix="assets",
            created_by=alice,
        )
    row.alice = alice
    return row


def _proxy_upload(drive: Any, payload: bytes, **draft_kwargs: Any) -> Any:
    """Run the draft → push → finalize cycle as the drive owner."""

    with actor_context(drive.alice):
        row = File.objects.draft(
            filename="test.png",
            mime_type="image/png",
            size_bytes=len(payload),
            drive_id=str(drive.sqid),
            **draft_kwargs,
        )
        if row.upload_state == UploadState.READY:
            return row
        token = row.issue_upload_token()
        File.objects.for_upload_token(token).receive_bytes(BytesIO(payload))
        fresh = File.objects.all().from_public_id(str(row.sqid))
        return fresh.finalize(expected_hash=hashlib.sha256(payload).hexdigest(), expected_size=len(payload))


@pytest.mark.django_db(transaction=True)
def test_proxy_upload_flow_verifies_bytes_and_dedups(tmp_path: Path, drive: Any) -> None:
    """Begin, push, and finalize converge on a READY row; identical bytes dedup."""

    row = _proxy_upload(drive, PNG_BYTES)

    assert row.upload_state == UploadState.READY
    assert row.content_hash == PNG_SHA256
    assert row.size_bytes == len(PNG_BYTES)
    assert row.mime_type is not None and row.mime_type.mime_type == "image/png"
    assert row.created_by_id == drive.alice.pk
    stored = tmp_path / row.storage_path
    assert stored.read_bytes() == PNG_BYTES
    assert row.url.startswith("/media/")

    with actor_context(drive.alice):
        dedup = File.objects.draft(
            filename="copy.png",
            size_bytes=len(PNG_BYTES),
            drive_id=str(drive.sqid),
            content_hash=PNG_SHA256,
        )
    assert dedup.pk == row.pk
    assert dedup.upload_state == UploadState.READY


@pytest.mark.django_db(transaction=True)
def test_proxy_upload_token_is_one_shot(drive: Any) -> None:
    """A consumed upload token cannot push bytes twice."""

    with actor_context(drive.alice):
        row = File.objects.draft(filename="once.bin", drive_id=str(drive.sqid))
        token = row.issue_upload_token()
        File.objects.for_upload_token(token).receive_bytes(BytesIO(b"first"))
        with pytest.raises(exceptions.UploadDenied):
            File.objects.for_upload_token(token).receive_bytes(BytesIO(b"second"))


@pytest.mark.django_db(transaction=True)
def test_finalize_rejects_mismatched_bytes(tmp_path: Path, drive: Any) -> None:
    """A hash mismatch fails the row and removes the backend object."""

    with actor_context(drive.alice):
        row = File.objects.draft(filename="bad.bin", drive_id=str(drive.sqid))
        token = row.issue_upload_token()
        File.objects.for_upload_token(token).receive_bytes(BytesIO(PNG_BYTES))
        with pytest.raises(exceptions.UploadConflict):
            File.objects.all().from_public_id(str(row.sqid)).finalize(
                expected_hash="0" * 64,
                expected_size=len(PNG_BYTES),
            )
    row.refresh_from_db()
    assert row.upload_state == UploadState.FAILED
    assert row.upload_envelope["failure_reason"] == "hash_mismatch"
    assert not (tmp_path / row.storage_path).exists()


@pytest.mark.django_db(transaction=True)
def test_proxy_upload_enforces_the_byte_cap(tmp_path: Path, drive: Any, settings: Any) -> None:
    """A body above ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES is rejected and cleaned up."""

    settings.ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES = 8
    with actor_context(drive.alice):
        row = File.objects.draft(filename="big.bin", drive_id=str(drive.sqid))
        token = row.issue_upload_token()
        with pytest.raises(exceptions.UploadTooLarge):
            File.objects.for_upload_token(token).receive_bytes(BytesIO(b"way more than eight bytes"))
    row.refresh_from_db()
    assert row.upload_state == UploadState.FAILED
    assert not (tmp_path / row.storage_path).exists()


@pytest.mark.django_db(transaction=True)
def test_other_users_cannot_target_the_drive(drive: Any) -> None:
    """An unrelated user cannot draft files into someone else's drive."""

    stranger = get_user_model().objects.create_user(username="storage-bob", email="bob@example.com")
    with actor_context(stranger), pytest.raises(exceptions.UploadError):
        File.objects.draft(filename="nope.bin", drive_id=str(drive.sqid))


@pytest.mark.django_db(transaction=True)
def test_upload_rejects_folders_from_other_drives(tmp_path: Path, drive: Any) -> None:
    """draft refuses a folder that does not belong to the target drive."""

    with system_context(reason="test storage setup"):
        other = Drive._base_manager.create(
            backend=drive.backend,
            slug="other",
            name="Other",
            prefix="other",
            created_by=drive.alice,
        )
        folder = Folder._base_manager.create(drive=other, name="Inbox", created_by=drive.alice)
    with actor_context(drive.alice), pytest.raises(exceptions.UploadConflict):
        File.objects.draft(
            filename="misplaced.bin",
            drive_id=str(drive.sqid),
            folder_id=str(folder.sqid),
        )


@pytest.mark.django_db(transaction=True)
def test_soft_delete_trash_restore_and_purge(tmp_path: Path, drive: Any) -> None:
    """delete() trashes, restore() reverses, purge() removes row and bytes."""

    row = _proxy_upload(drive, PNG_BYTES)
    with actor_context(drive.alice):
        row.delete()
    row.refresh_from_db()
    assert row.is_trashed and row.trashed_at is not None
    assert row.trashed_by == drive.alice
    assert (tmp_path / row.storage_path).exists()
    with actor_context(drive.alice):
        assert File.objects.trashed().filter(pk=row.pk).exists()
        row.restore()
    row.refresh_from_db()
    assert not row.is_trashed and row.trashed_by is None

    storage_path = row.storage_path
    with system_context(reason="test storage purge"):
        row.purge()
    assert not File._base_manager.filter(content_hash=PNG_SHA256).exists()
    assert not (tmp_path / storage_path).exists()


@pytest.mark.django_db(transaction=True)
def test_folder_tree_invariants(drive: Any) -> None:
    """Root names are unique per drive and a folder cannot contain itself."""

    from django.core.exceptions import ValidationError
    from django.db import IntegrityError

    with system_context(reason="test storage folders"):
        root = Folder._base_manager.create(drive=drive, name="Docs", created_by=drive.alice)
        child = Folder._base_manager.create(drive=drive, parent=root, name="Inner", created_by=drive.alice)
        with pytest.raises(IntegrityError):
            Folder._base_manager.create(drive=drive, name="Docs", created_by=drive.alice)

    with system_context(reason="test storage folders"):
        root.refresh_from_db()
        root.parent = child
        with pytest.raises(ValidationError):
            root.full_clean()


@pytest.mark.django_db(transaction=True)
def test_dedup_restores_a_trashed_hit(drive: Any) -> None:
    """draft with a known hash revives the trashed row instead of dooming it."""

    row = _proxy_upload(drive, PNG_BYTES)
    with actor_context(drive.alice):
        row.delete()
        dedup = File.objects.draft(
            filename="again.png",
            size_bytes=len(PNG_BYTES),
            drive_id=str(drive.sqid),
            content_hash=PNG_SHA256,
        )
    assert dedup.pk == row.pk
    dedup.refresh_from_db()
    assert not dedup.is_trashed


@pytest.mark.django_db(transaction=True)
def test_create_folder_factory_gates_on_drive_write(drive: Any) -> None:
    """The factory creates for a drive writer and denies an unrelated user."""

    with actor_context(drive.alice):
        folder = Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Reports")
    assert folder.pk is not None and folder.drive_id == drive.pk and not folder.is_virtual

    # An unrelated user can't create in a drive they have no write access to
    # (the drive isn't even readable to them, so it resolves as not-found).
    stranger = get_user_model().objects.create_user(username="storage-dora", email="dora@example.com")
    with actor_context(stranger), pytest.raises(exceptions.UploadError):
        Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Sneaky")


@pytest.mark.django_db(transaction=True)
def test_bulk_delete_is_refused(drive: Any) -> None:
    """A queryset delete cannot bypass soft-trash and orphan backend bytes."""

    _proxy_upload(drive, PNG_BYTES)
    with actor_context(drive.alice), pytest.raises(NotImplementedError):
        File.objects.all().delete()


@pytest.mark.django_db(transaction=True)
def test_proxy_upload_view_streams_for_actor_and_rejects_reuse_and_anon(drive: Any) -> None:
    """The proxy view maps the actor + token to a stream, then refuses reuse/anon.

    Exercises the view's own job — token extraction, the streamed PUT body, and
    the ``UploadError`` → JSON-status mapping. Resolving the request actor from
    a session/credential is the actor middleware's concern, covered elsewhere;
    here the actor is pinned directly.
    """

    import json

    from django.test import RequestFactory

    from angee.storage import views

    with actor_context(drive.alice):
        row = File.objects.draft(filename="v.png", mime_type="image/png", drive_id=str(drive.sqid))
        token = row.issue_upload_token()

    def put() -> Any:
        return RequestFactory().put(
            f"/storage/upload?token={token}",
            data=PNG_BYTES,
            content_type="application/octet-stream",
        )

    anon = views.upload(put())
    assert anon.status_code == 403  # no actor → receive_bytes denies

    with actor_context(drive.alice):
        ok = views.upload(put())
        assert ok.status_code == 200 and json.loads(ok.content)["id"] == str(row.sqid)
        reused = views.upload(put())
        assert reused.status_code == 403  # one-shot token already spent


@pytest.mark.django_db(transaction=True)
def test_users_get_a_trash_smart_folder(storage_tables: None) -> None:
    """Creating a user creates exactly one owned Trash smart folder."""

    del storage_tables
    user = get_user_model().objects.create_user(username="storage-carol", email="carol@example.com")
    folders = Folder._base_manager.filter(owner=user, is_virtual=True)
    assert [folder.smart_kind for folder in folders] == [Folder.SmartKind.TRASH]
    assert folders.get().drive_id is None
