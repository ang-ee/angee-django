"""Tests for the storage upload protocol and file lifecycle."""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.db.models.signals import post_save
from rebac import actor_context, app_settings, system_context
from rebac.actors import to_subject_ref
from rebac.errors import PermissionDenied
from rebac.roles import grant

from angee.storage import exceptions
from angee.storage.models import FileManager, UploadState
from angee.storage.signals import file_finalized
from tests.conftest import (
    STORAGE_TEST_MODELS,
    Backend,
    Drive,
    File,
    Folder,
    MimeType,
    _clear_model_tables,
    _create_missing_tables,
    addon_schema,
    execute_schema,
    result_data,
)

# A real 1x1 PNG — libmagic classifies it as image/png; a fake signature would not.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)
PNG_SHA256 = hashlib.sha256(PNG_BYTES).hexdigest()
storage_schema = importlib.import_module("angee.storage.schema")


def test_file_source_model_owns_the_upload_protocol() -> None:
    """The factory lives on the manager; byte intake and publish on the row."""

    from angee.storage.models import File as AbstractFile

    assert AbstractFile._meta.abstract is True
    assert AbstractFile.is_runtime_model() is True
    assert isinstance(File.objects, FileManager)
    assert hasattr(File.objects, "draft")
    assert all(hasattr(AbstractFile, verb) for verb in ("receive_bytes", "finalize", "issue_upload_token"))
    assert set(UploadState.values) == {"draft", "ready", "failed"}


def test_storage_autoconfig_has_no_runtime_setting_shim() -> None:
    """Production storage code reads declared Django settings directly."""

    autoconfig = importlib.import_module("angee.storage.autoconfig")

    assert not hasattr(autoconfig, "setting")


def test_backend_has_no_dormant_default_flag() -> None:
    """The configured default drive owns defaults; backend rows do not."""

    assert "is_default" not in {field.name for field in Backend._meta.fields}


def test_detect_mime_falls_back_to_the_filename_when_libmagic_is_unsure() -> None:
    """libmagic wins on content it recognises; an opaque blob defers to the
    filename extension, so a format libmagic misses (e.g. HEIC) still gets a
    real type instead of the generic catch-all."""

    from angee.storage.uploads import detect_mime

    # Recognised content beats a misleading extension.
    assert detect_mime(PNG_BYTES, "trick.heic") == "image/png"
    # An opaque blob (libmagic → octet-stream) defers to the extension.
    assert detect_mime(b"\x00\x01\x02\x03", "IMG_9803.HEIC") == "image/heic"
    # Nothing to sniff and no name stays the catch-all.
    assert detect_mime(b"\x00\x01\x02\x03") == "application/octet-stream"


@pytest.fixture
def storage_tables() -> Iterator[None]:
    """Provide the concrete storage tables for one test."""

    created_models = _create_missing_tables(STORAGE_TEST_MODELS)
    try:
        yield
    finally:
        _clear_model_tables(STORAGE_TEST_MODELS)
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
            backend_class="local",
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
def test_finalize_publishes_once_when_stale_instance_loses_ready_race(drive: Any) -> None:
    """Only the caller that conditionally flips DRAFT→READY emits file_finalized."""

    seen: list[int] = []

    def capture(sender: Any, instance: Any, **kwargs: Any) -> None:
        del sender, kwargs
        seen.append(instance.pk)

    file_finalized.connect(capture, sender=File, dispatch_uid="test.storage.finalize_once")
    try:
        with actor_context(drive.alice):
            row = File.objects.draft(
                filename="race.png",
                mime_type="image/png",
                size_bytes=len(PNG_BYTES),
                drive_id=str(drive.sqid),
            )
            token = row.issue_upload_token()
            File.objects.for_upload_token(token).receive_bytes(BytesIO(PNG_BYTES))
            stale = File.objects.all().from_public_id(str(row.sqid))
            fresh = File.objects.all().from_public_id(str(row.sqid))

            fresh.finalize(expected_hash=PNG_SHA256, expected_size=len(PNG_BYTES))
            stale.finalize(expected_hash=PNG_SHA256, expected_size=len(PNG_BYTES))
    finally:
        file_finalized.disconnect(sender=File, dispatch_uid="test.storage.finalize_once")

    assert seen == [row.pk]


@pytest.mark.django_db(transaction=True)
def test_finalize_emits_post_save_for_change_feed(drive: Any) -> None:
    """The DRAFT->READY conditional update still reaches post_save publishers."""

    seen: list[frozenset[str]] = []
    with actor_context(drive.alice):
        row = File.objects.draft(
            filename="change-feed.png",
            mime_type="image/png",
            size_bytes=len(PNG_BYTES),
            drive_id=str(drive.sqid),
        )
        token = row.issue_upload_token()
        File.objects.for_upload_token(token).receive_bytes(BytesIO(PNG_BYTES))
        fresh = File.objects.all().from_public_id(str(row.sqid))

    def capture(sender: Any, instance: Any, created: bool, update_fields: Any, **kwargs: Any) -> None:
        del sender, kwargs
        if instance.pk == row.pk and not created:
            seen.append(frozenset(update_fields or ()))

    post_save.connect(capture, sender=File, dispatch_uid="test.storage.finalize_post_save")
    try:
        with actor_context(drive.alice):
            fresh.finalize(expected_hash=PNG_SHA256, expected_size=len(PNG_BYTES))
    finally:
        post_save.disconnect(sender=File, dispatch_uid="test.storage.finalize_post_save")

    assert frozenset({"content_hash", "size_bytes", "mime_type", "upload_state", "updated_at"}) in seen


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


def test_storage_resource_metadata_exposes_delete_previews() -> None:
    """Storage's custom delete verbs are advertised to refine/resource actions."""

    schema = addon_schema(storage_schema.schemas, "public")
    resources = {item.model_label: item for item in schema.angee_resources}

    assert resources["storage.File"].roots.delete_preview_name == "delete_file"
    assert resources["storage.Folder"].roots.delete_preview_name == "delete_folder"
    assert resources["storage.File"].type_names.delete_payload == "DeletePreview"
    assert resources["storage.Folder"].type_names.delete_payload == "DeletePreview"


@pytest.mark.django_db(transaction=True)
def test_storage_graphql_custom_mutations_accept_public_ids(drive: Any) -> None:
    """Custom storage mutations resolve raw sqids at the GraphQL boundary."""

    schema = addon_schema(storage_schema.schemas, "public")
    with actor_context(drive.alice):
        row = File.objects.draft(
            filename="graphql.png",
            mime_type="image/png",
            size_bytes=len(PNG_BYTES),
            drive_id=str(drive.sqid),
        )
        token = row.issue_upload_token()
        File.objects.for_upload_token(token).receive_bytes(BytesIO(PNG_BYTES))

    finalized = result_data(
        execute_schema(
            schema,
            """
            mutation Finalize($input: FileUploadFinalizeInput!) {
              file_upload_finalize(input: $input) {
                error
                error_code
                file { id upload_state }
              }
            }
            """,
            {
                "input": {
                    "file": str(row.sqid),
                    "content_hash": PNG_SHA256,
                    "size_bytes": len(PNG_BYTES),
                }
            },
            user=drive.alice,
        )
    )["file_upload_finalize"]
    assert finalized == {
        "error": None,
        "error_code": None,
        "file": {"id": str(row.sqid), "upload_state": "READY"},
    }

    row.refresh_from_db()
    storage_path = row.storage_path
    deleted = result_data(
        execute_schema(
            schema,
            """
            mutation Delete($id: ID!) {
              delete_file(id: $id, confirm: true) { has_blockers }
            }
            """,
            {"id": str(row.sqid)},
            user=drive.alice,
        )
    )["delete_file"]
    assert deleted == {"has_blockers": False}
    row.refresh_from_db()
    assert row.is_trashed
    assert (Path(drive.backend.storage.location) / storage_path).exists()

    restored = result_data(
        execute_schema(
            schema,
            """
            mutation Restore($id: ID!) {
              restore_file(id: $id) { id is_trashed }
            }
            """,
            {"id": str(row.sqid)},
            user=drive.alice,
        )
    )["restore_file"]
    assert restored == {"id": str(row.sqid), "is_trashed": False}


@pytest.mark.django_db(transaction=True)
def test_storage_graphql_purge_accepts_public_id(tmp_path: Path, drive: Any) -> None:
    """The admin purge mutation resolves its raw sqid through the shared owner."""

    admin = get_user_model().objects.create_user(
        username="storage-graphql-admin",
        email="storage-admin@example.com",
        password="admin",
    )
    grant(actor=admin, role="storage/role:storage_admin")
    row = _proxy_upload(drive, PNG_BYTES)
    storage_path = row.storage_path
    result = result_data(
        execute_schema(
            addon_schema(storage_schema.schemas, "console"),
            """
            mutation Purge($id: ID!) {
              purge_file(id: $id)
            }
            """,
            {"id": str(row.sqid)},
            user=admin,
        )
    )["purge_file"]

    assert result is True
    assert not File._base_manager.filter(pk=row.pk).exists()
    assert not (tmp_path / storage_path).exists()


@pytest.mark.django_db(transaction=True)
def test_create_drive_gates_on_the_rebac_create_rule(drive: Any) -> None:
    """The ``storage/drive`` ``create`` rule authorizes admins and denies others.

    The de-elevated drive Hasura insert carries no GraphQL gate and no elevated write:
    the id-less insert is authorized by ``create = admin->member + manager``
    (mirroring ``storage/backend``) against the request actor. Tested at the model
    owner so the create rule is isolated from FK-read redaction: the const-arrow
    ``admin->member`` resolves without a per-object tuple, so a platform admin
    creates while an unprivileged actor is denied with ``PermissionDenied`` and an
    anonymous request resolves no actor. (The ``manager`` arm needs a per-object
    ``manager`` tuple an id-less insert cannot carry — the same admin-only outcome
    ``storage/backend`` already relies on.)
    """

    admin = get_user_model().objects.create_superuser(
        username="storage-create-admin",
        email="create-admin@example.com",
        password="admin",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    stranger = get_user_model().objects.create_user(username="storage-create-bob", email="create-bob@example.com")

    with actor_context(admin):
        created = Drive._default_manager.create(backend=drive.backend, slug="admin-drive", name="Admin Drive")
    assert created.pk is not None and str(created.created_by_id) == str(admin.pk)

    with actor_context(stranger), pytest.raises(PermissionDenied):
        Drive._default_manager.create(backend=drive.backend, slug="bob-drive", name="Bob Drive")
    with actor_context(AnonymousUser()), pytest.raises(PermissionDenied):
        Drive._default_manager.create(backend=drive.backend, slug="anon-drive", name="Anon Drive")
    with system_context(reason="test drive create denial"):
        assert not Drive._base_manager.filter(slug__in=["bob-drive", "anon-drive"]).exists()


@pytest.mark.django_db(transaction=True)
def test_create_drive_graphql_surface_is_de_elevated(drive: Any) -> None:
    """The de-elevated console Hasura insert creates for a platform admin.

    Proves removing the drive GraphQL ``permission_classes``/``write_context``
    pair is safe: the admin's create flows through the stock strawberry-django
    create (FK resolved by sqid) and the REBAC create signal authorizes the insert,
    with no GraphQL gate and no elevated write.
    """

    admin = get_user_model().objects.create_superuser(
        username="storage-graphql-create-admin",
        email="gql-create-admin@example.com",
        password="admin",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)

    created = result_data(
        execute_schema(
            addon_schema(storage_schema.schemas, "console"),
            """
            mutation CreateDrive($input: drives_insert_input!) {
              insert_drives_one(object: $input) { id slug name }
            }
            """,
            {"input": {"backend": str(drive.backend.sqid), "slug": "gql-drive", "name": "GQL Drive"}},
            user=admin,
        )
    )["insert_drives_one"]
    assert created["slug"] == "gql-drive"
    with system_context(reason="test drive create graphql"):
        assert Drive._base_manager.filter(slug="gql-drive").exists()


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
        with pytest.raises(ValidationError):
            root.save(update_fields=["parent"])
        root.refresh_from_db()
        assert root.parent_id is None


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
def test_ingest_dedup_grants_read_reach_to_second_owner(drive: Any) -> None:
    """A dedup hit grants the requested owner reach to the existing READY row."""

    bob = get_user_model().objects.create_user(username="storage-dedup-bob", email="dedup-bob@example.com")
    with system_context(reason="test storage dedup ingest"):
        first = File.objects.ingest_bytes(
            PNG_BYTES,
            filename="first.png",
            owner_id=drive.alice.pk,
            drive_id=str(drive.sqid),
        )
        second = File.objects.ingest_bytes(
            PNG_BYTES,
            filename="second.png",
            owner_id=bob.pk,
            drive_id=str(drive.sqid),
        )

    assert second.pk == first.pk
    with actor_context(bob):
        assert File.objects.filter(pk=first.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_create_folder_factory_gates_on_drive_write(drive: Any) -> None:
    """The factory creates for a drive writer and denies an unrelated user."""

    with actor_context(drive.alice):
        folder = Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Reports")
    assert folder.pk is not None and folder.drive_id == drive.pk and not folder.is_virtual
    assert not folder.is_sudo()
    assert folder.actor() == to_subject_ref(drive.alice)

    # An unrelated user can't create in a drive they have no write access to
    # (the drive isn't even readable to them, so it resolves as not-found).
    stranger = get_user_model().objects.create_user(username="storage-dora", email="dora@example.com")
    with actor_context(stranger), pytest.raises(exceptions.UploadError):
        Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Sneaky")


@pytest.mark.django_db(transaction=True)
def test_file_draft_rebinds_returned_row_to_actor(drive: Any) -> None:
    """The draft factory returns an actor-bound row after the sudo insert."""

    with actor_context(drive.alice):
        row = File.objects.draft(filename="draft.txt", drive_id=str(drive.sqid))

    assert row.pk is not None
    assert not row.is_sudo()
    assert row.actor() == to_subject_ref(drive.alice)


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
def test_proxy_download_sets_content_cache_headers_and_honors_etag(
    drive: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content-addressed downloads advertise and honor validators."""

    from django.test import RequestFactory

    from angee.storage import views
    from angee.storage.uploads import DOWNLOAD_TOKEN_HEADER, DOWNLOAD_TOKEN_MAX_AGE

    row = _proxy_upload(drive, PNG_BYTES)
    token = row.issue_download_token()
    request = RequestFactory().get(f"/storage/download/{row.filename}?token={token}")

    ok = views.download(request, row.filename)
    assert ok.status_code == 200
    etag = ok["ETag"]
    assert etag == f'"{PNG_SHA256}"'
    assert f"max-age={DOWNLOAD_TOKEN_MAX_AGE}" in ok["Cache-Control"]
    assert "private" in ok["Cache-Control"]
    assert "immutable" in ok["Cache-Control"]
    assert _header_values(ok["Vary"]) == {DOWNLOAD_TOKEN_HEADER.lower(), "authorization"}
    ok.close()

    header_token = views.download(
        RequestFactory().get(
            f"/storage/download/{row.filename}",
            HTTP_X_ANGEE_DOWNLOAD_TOKEN=token,
        ),
        row.filename,
    )
    assert header_token.status_code == 200
    assert _header_values(header_token["Vary"]) == {DOWNLOAD_TOKEN_HEADER.lower(), "authorization"}
    header_token.close()

    cached = RequestFactory().get(
        f"/storage/download/{row.filename}?token={token}",
        HTTP_IF_NONE_MATCH=etag,
    )
    monkeypatch.setattr(File, "open_stream", lambda self: pytest.fail("304 must not reopen stored bytes"))
    not_modified = views.download(cached, row.filename)
    assert not_modified.status_code == 304
    assert not_modified["ETag"] == etag
    assert "immutable" in not_modified["Cache-Control"]
    assert _header_values(not_modified["Vary"]) == {DOWNLOAD_TOKEN_HEADER.lower(), "authorization"}

    failed_precondition = views.download(
        RequestFactory().get(
            f"/storage/download/{row.filename}?token={token}",
            HTTP_IF_MATCH='"not-the-content-hash"',
        ),
        row.filename,
    )
    assert failed_precondition.status_code == 412
    assert "immutable" not in failed_precondition.get("Cache-Control", "")


def _header_values(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",")}


@pytest.mark.django_db(transaction=True)
def test_users_get_a_trash_smart_folder(storage_tables: None) -> None:
    """Creating a user creates exactly one owned Trash smart folder."""

    del storage_tables
    user = get_user_model().objects.create_user(username="storage-carol", email="carol@example.com")
    folders = Folder._base_manager.filter(owner=user, is_virtual=True)
    assert [folder.smart_kind for folder in folders] == [Folder.SmartKind.TRASH]
    assert folders.get().drive_id is None


@pytest.mark.django_db(transaction=True)
def test_backend_storage_cache_tracks_resolved_env_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_tables: None,
) -> None:
    """Environment-backed backend config changes produce a new storage instance."""

    del storage_tables
    Backend._storage_cache.clear()
    monkeypatch.setenv("ANGEE_TEST_STORAGE_ROOT", str(tmp_path / "one"))
    with system_context(reason="test storage setup"):
        backend = Backend._base_manager.create(
            slug="env-local",
            label="Env Local",
            backend_class="local",
            backend_config={"root": {"env": "ANGEE_TEST_STORAGE_ROOT"}, "base_url": "/media/"},
        )

    first = backend.storage
    monkeypatch.setenv("ANGEE_TEST_STORAGE_ROOT", str(tmp_path / "two"))
    second = backend.storage

    assert first is not second
    assert Path(first.location) == tmp_path / "one"
    assert Path(second.location) == tmp_path / "two"


@pytest.mark.django_db(transaction=True)
def test_backend_storage_cache_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_tables: None,
) -> None:
    """The process cache evicts old resolved backend instances."""

    del storage_tables
    from angee.storage import models as storage_models

    monkeypatch.setattr(storage_models, "_STORAGE_CACHE_MAX_SIZE", 2)
    Backend._storage_cache.clear()
    with system_context(reason="test storage setup"):
        backends = [
            Backend._base_manager.create(
                slug=f"bounded-{index}",
                label=f"Bounded {index}",
                backend_class="local",
                backend_config={"root": str(tmp_path / str(index)), "base_url": "/media/"},
            )
            for index in range(3)
        ]

    for backend in backends:
        backend.storage

    assert len(Backend._storage_cache) == 2
