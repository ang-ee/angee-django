"""Parent redaction, bearer revocation, canonical attachment and file audiences."""

from typing import Any

import pytest
from django.core import signing
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context, to_subject_ref
from rebac.actors import NoActorResolvedError
from rebac.errors import PermissionDenied
from rebac.models import active_relationship_model

from angee.base.errors import RecordAccessSubjectRefused
from angee.base.identity import public_subject_ref
from angee.base.refs import canonical_record_target
from angee.graphql.relations import actor_scoped_public_id, actor_scoped_to_one
from angee.storage import exceptions, views
from angee.storage import schema as storage_schema
from angee.storage.models import FileAttachmentManager, FileVisibility
from angee.storage.uploads import DOWNLOAD_TOKEN_SALT
from tests.conftest import (
    Drive,
    File,
    FileAttachment,
    Folder,
    MimeType,
    addon_schema,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
)
from tests.mtidemo.models import MtiChild, MtiChildProxy, MtiParent
from tests.storage_campaign import relationship_storage as relationship_storage
from tests.test_storage import PNG_BYTES, _proxy_upload
from tests.test_storage import drive as drive

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("schema_name", ["public", "console"])
def test_record_files_query_requires_record_and_file_read_and_reports_arm(
    drive: Any, monkeypatch: pytest.MonkeyPatch, schema_name: str,
) -> None:
    reader, stranger = create_user("record-files-reader"), create_user("record-files-stranger")
    with system_context(reason="test.storage.record_files.seed"):
        file = File.objects.create(
            drive=drive, filename="attached.txt", content_hash="d" * 64, visibility="record",
            owner=drive.alice, upload_state="ready",
        )
        attachment = FileAttachment.objects.attach(file, drive)
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).grant_record_access("viewer", reader)
    assert not FileAttachment.objects.has_record_arm(canonical_record_target(drive))
    # The capability branch is isolated here; the native arm's actual presence
    # is covered by record upload tests, and the file edge still scopes itself.
    monkeypatch.setattr(FileAttachmentManager, "has_record_arm", lambda *_: True)
    schema = addon_schema(storage_schema.schemas, schema_name)
    query = """query ($model: String!, $id: ID!) {
      record_files(model_label: $model, record_id: $id) {
        available can_upload attachments { id file { id filename } }
      }
    }"""
    variables = {"model": "storage.Drive", "id": str(drive.sqid)}
    visible = result_data(execute_schema(schema, query, variables, user=reader))["record_files"]
    assert visible == {
        "available": True, "can_upload": False,
        "attachments": [],
    }
    owner_view = result_data(execute_schema(schema, query, variables, user=drive.alice))["record_files"]
    assert owner_view == {
        "available": True, "can_upload": True,
        "attachments": [{"id": str(attachment.sqid),
                         "file": {"id": str(file.sqid), "filename": "attached.txt"}}],
    }
    assert result_data(execute_schema(schema, query, variables, user=stranger))["record_files"] == {
        "available": False, "can_upload": False, "attachments": [],
    }
    monkeypatch.undo()
    no_arm = result_data(execute_schema(schema, query, {
        "model": "storage.Drive", "id": str(drive.sqid),
    }, user=drive.alice))["record_files"]
    assert no_arm == {"available": False, "can_upload": False, "attachments": []}


@pytest.mark.parametrize("schema_name", ["public", "console"])
@pytest.mark.parametrize("parents_readable", [False, True])
def test_file_parent_ids_are_redacted_and_batched_at_six_and_twenty_four_rows(
    drive: Any, schema_name: str, parents_readable: bool,
) -> None:
    reader = create_user("parent-projection-reader")
    with system_context(reason="test.storage.parents.seed"):
        folders = [Folder.objects.create(drive=drive, name=f"Folder {i}") for i in range(12)]
        rows = [File.objects.create(
            drive=drive, folder=folders[i // 2], filename=f"{i:02}.txt", content_hash=f"{i:064x}",
        ) for i in range(24)]
        for row in rows:
            row.grant_record_access("viewer", reader)
    if parents_readable:
        with actor_context(drive.alice):
            drive.with_actor(drive.alice).grant_record_access("viewer", reader)
    schema = addon_schema(storage_schema.schemas, schema_name)
    counts = []
    for limit in (6, 24):
        with CaptureQueriesContext(connection) as captured:
            data = result_data(execute_schema(schema, """
                query Files($limit: Int!) {
                  files(limit: $limit, order_by: [{filename: asc}]) { id drive folder }
                }
            """, {"limit": limit}, user=reader))["files"]
        assert data == [
            {"id": str(row.sqid), "drive": str(drive.sqid) if parents_readable else None,
             "folder": str(folders[i // 2].sqid) if parents_readable else None}
            for i, row in enumerate(rows[:limit])
        ]
        counts.append(len(captured))
    assert counts[0] == counts[1], f"Parent projections added per-row queries: {counts}"


def test_folder_and_drive_projections_hide_unreadable_parents(drive: Any) -> None:
    reader = create_user("folder-only-reader")
    with system_context(reason="test.storage.folder.parents"):
        parent = Folder.objects.create(drive=drive, name="Private parent")
        child = Folder.objects.create(drive=drive, parent=parent, owner=reader, name="Visible child")
    schema = addon_schema(storage_schema.schemas, "public")
    query = "query Folder($id: String!) { folders_by_pk(id: $id) { id drive parent } }"
    expected = {"id": str(child.sqid), "drive": None, "parent": None}
    assert result_data(execute_schema(schema, query, {"id": str(child.sqid)}, user=reader))["folders_by_pk"] == expected
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).grant_record_access("viewer", reader)
    expected.update(drive=str(drive.sqid), parent=str(parent.sqid))
    assert result_data(execute_schema(schema, query, {"id": str(child.sqid)}, user=reader))["folders_by_pk"] == expected
    backend_query = "query Drive($id: String!) { drives_by_pk(id: $id) { backend } }"
    assert result_data(execute_schema(
        schema, backend_query, {"id": str(drive.sqid)}, user=reader,
    ))["drives_by_pk"] == {"backend": None}
    admin = create_platform_admin("backend-reader")
    assert result_data(execute_schema(
        schema, backend_query, {"id": str(drive.sqid)}, user=admin,
    ))["drives_by_pk"] == {"backend": str(drive.backend.sqid)}


@pytest.mark.parametrize("factory", [actor_scoped_public_id, actor_scoped_to_one])
def test_guarded_relation_rechecks_sudo_cache_and_reuses_actor_scoped_cache(
    drive: Any, factory: Any, django_assert_num_queries: Any,
) -> None:
    reader = create_user("guarded-reader")
    with system_context(reason="test.storage.cached.parent"):
        row = File.objects.create(drive=drive, filename="cache.txt", content_hash="a" * 64)
        row = File.objects.select_related("drive").get(pk=row.pk)
    resolver = factory("drive").base_resolver.wrapped_func
    with actor_context(reader):
        assert resolver(row) is None
    with actor_context(drive.alice):
        row.drive = Drive.objects.get(pk=drive.pk)
        with django_assert_num_queries(0):
            result = resolver(row)
        assert (str(result) if factory is actor_scoped_public_id else result.pk) == (
            str(drive.sqid) if factory is actor_scoped_public_id else drive.pk
        )
        row._state.fields_cache["drive"] = None
        with django_assert_num_queries(0):
            assert resolver(row) is None
        empty = File(drive=drive, filename="no-folder.txt")
        with django_assert_num_queries(0):
            assert factory("folder").base_resolver.wrapped_func(empty) is None


def test_download_bearer_rechecks_issuing_actor_before_conditional_response(drive: Any) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    reader = create_user("download-viewer")
    with actor_context(drive.alice):
        row.with_actor(drive.alice).grant_record_access("viewer", reader)
    with actor_context(reader):
        token = row.issue_download_token()
    claims = signing.loads(token, salt=DOWNLOAD_TOKEN_SALT)
    assert claims == {"file": str(row.sqid), "actor": str(public_subject_ref(to_subject_ref(reader)))}
    explicit = row.issue_download_token(actor=reader)
    assert signing.loads(explicit, salt=DOWNLOAD_TOKEN_SALT) == claims
    request = RequestFactory().get(f"/download?token={token}")
    response = views.download(request, row.filename)
    assert response.status_code == 200
    etag = response["ETag"]
    assert b"".join(response.streaming_content) == PNG_BYTES
    response.close()
    conditional = RequestFactory().get(f"/download?token={token}", HTTP_IF_NONE_MATCH=etag)
    assert views.download(conditional, row.filename).status_code == 304
    with actor_context(drive.alice):
        row.with_actor(drive.alice).revoke_record_access("viewer", reader)
        # Request/ambient authority must not replace the token's revoked subject.
        assert views.download(conditional, row.filename).status_code == 403
        assert views.download(request, row.filename).status_code == 403


@pytest.mark.parametrize("actor_claim", [None, "", "not-a-subject", "auth/user:not-a-public-id"])
def test_download_rejects_legacy_and_malformed_actor_claims(drive: Any, actor_claim: Any) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    claims = {"file": str(row.sqid)}
    if actor_claim is not None:
        claims["actor"] = actor_claim
    token = signing.dumps(claims, salt=DOWNLOAD_TOKEN_SALT)
    with pytest.raises(exceptions.UploadDenied):
        File.objects.for_download_token(token)
    with system_context(reason="test.storage.actorless.token"), pytest.raises(NoActorResolvedError):
        row.issue_download_token()


@pytest.mark.parametrize("missing", ["file_read", "target_write", "untyped_target"])
def test_attach_rejects_missing_authority_even_with_sudo_loaded_inputs(drive: Any, missing: str) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    reader = create_user("attach-reader")
    with system_context(reason="test.storage.attach.seed"):
        target = Drive.objects.create(backend=drive.backend, slug="target", name="Target", owner=reader)
        row = File.objects.get(pk=row.pk)
    if missing == "target_write":
        target = drive
        with actor_context(drive.alice):
            row.with_actor(drive.alice).grant_record_access("viewer", reader)
    elif missing == "untyped_target":
        target = MimeType._base_manager.first()
        with actor_context(drive.alice):
            row.with_actor(drive.alice).grant_record_access("viewer", reader)
    with actor_context(reader), pytest.raises(PermissionDenied):
        FileAttachment.objects.attach(row, target)
    with system_context(reason="test.storage.attach.inspect"):
        assert FileAttachment.objects.count() == 0


def test_attach_converges_on_canonical_parent_and_proxy_with_actor_scoped_result(drive: Any) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    admin = create_platform_admin("canonical-attachment-admin")
    with system_context(reason="test.storage.attach.canonical"):
        child = MtiChild.objects.create(title="Parent", detail="Child")
        parent = MtiParent.objects.get(pk=child.pk)
        proxy = MtiChildProxy.objects.get(pk=child.pk)
        first = FileAttachment.objects.attach(row, child)
    with actor_context(admin):
        second = FileAttachment.objects.attach(row, parent)
        third = FileAttachment.objects.attach(row, proxy)
        assert second.actor() == third.actor()
        assert second.actor() is not None
    assert first.pk == second.pk == third.pk
    assert first.content_type.model_class() is MtiParent


def test_narrowing_removes_container_access_but_keeps_owner_and_named_viewer(drive: Any) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    reader, named = create_user("container-reader"), create_user("named-file-viewer")
    with actor_context(drive.alice):
        row.folder = Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Inherited folder")
        row.save(update_fields=["folder"])
        drive.with_actor(drive.alice).grant_record_access("viewer", reader)
        row.grant_record_access("viewer", named)
    with actor_context(reader):
        token = row.issue_download_token()
        with pytest.raises(PermissionDenied):
            row.with_actor(reader).set_visibility(FileVisibility.RECORD)
    with actor_context(drive.alice):
        row.with_actor(drive.alice).set_visibility(FileVisibility.RECORD)
    assert not row.with_actor(reader).has_access("read")
    assert row.with_actor(named).has_access("read")
    assert row.with_actor(drive.alice).has_access("read")
    with pytest.raises(exceptions.UploadDenied):
        File.objects.for_download_token(token)
    assert File.objects.for_download_token(row.issue_download_token(actor=named)).pk == row.pk


def test_file_owner_cannot_widen_without_drive_share_and_drive_editor_can_grant(drive: Any) -> None:
    editor, recipient = create_user("sharing-editor"), create_user("sharing-recipient")
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).grant_record_access("editor", editor)
    with actor_context(editor):
        row = File.objects.draft(filename="owned.txt", drive_id=str(drive.sqid))
        drive.with_actor(editor).grant_record_access("viewer", recipient)
        row.set_visibility(FileVisibility.RECORD)
        row.grant_record_access("viewer", recipient)
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).revoke_record_access("editor", editor)
    with actor_context(editor), pytest.raises(PermissionDenied):
        row.with_actor(editor).set_visibility(FileVisibility.INHERITED)
    with actor_context(drive.alice):
        row.with_actor(drive.alice).set_visibility(FileVisibility.INHERITED)
    assert row.visibility == FileVisibility.INHERITED


def test_container_owned_upload_loses_uploader_reach_after_narrowing(drive: Any) -> None:
    editor = create_user("container-owned-uploader")
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).owns_items = True
        drive.save(update_fields=["owns_items"])
        drive.grant_record_access("editor", editor)
    with actor_context(editor):
        row = File.objects.draft(filename="owned-by-container.txt", drive_id=str(drive.sqid))
        assert row.owner_id is None
        assert row.created_by_id == editor.pk
        row.set_visibility(FileVisibility.RECORD)
        assert not row.has_access("read")
        with pytest.raises(PermissionDenied):
            row.transfer_ownership(editor)


@pytest.mark.parametrize("operation", ["grant", "transfer", "dedup"])
def test_holder_refusal_blocks_every_storage_share_path_without_a_tuple(
    drive: Any, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    refused = create_user("refused-holder")
    with system_context(reason="test.storage.refusal.baseline"):
        count = active_relationship_model().objects.count()

    def refuse(self: Any, relation: str, subject: Any) -> None:
        raise RecordAccessSubjectRefused()

    monkeypatch.setattr(File, "validate_record_access_subject", refuse)
    with actor_context(drive.alice), pytest.raises(RecordAccessSubjectRefused):
        if operation == "grant":
            row.grant_record_access("viewer", refused)
        elif operation == "transfer":
            row.transfer_ownership(refused)
        else:
            File.objects.ingest_bytes(
                PNG_BYTES, filename="dedup.png", drive_id=str(drive.sqid), owner_id=refused.pk,
            )
    with system_context(reason="test.storage.refusal.inspect"):
        assert active_relationship_model().objects.count() == count
        row.refresh_from_db()
    assert row.owner_id == drive.alice.pk
    assert not row.with_actor(refused).has_access("read")


@pytest.mark.parametrize("visibility", ["RECORD", "INHERITED"])
@pytest.mark.parametrize("anonymous", [False, True])
def test_visibility_action_returns_same_failure_for_missing_and_unreadable_file(
    drive: Any, visibility: str, anonymous: bool,
) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    outsider = None if anonymous else create_user("visibility-outsider")
    schema = addon_schema(storage_schema.schemas, "public")
    payloads = [result_data(execute_schema(schema, """
        mutation Visibility($id: ID!, $visibility: FileVisibility!) {
          set_file_visibility(id: $id, visibility: $visibility) { ok message }
        }
    """, {"id": file_id, "visibility": visibility}, user=outsider))["set_file_visibility"]
        for file_id in (str(row.sqid), str(File(pk=row.pk + 1000).sqid))]
    assert payloads[0] == payloads[1]
    assert payloads[0]["ok"] is False
    with system_context(reason="test.storage.visibility.inspect"):
        row.refresh_from_db()
    assert row.visibility == FileVisibility.INHERITED


@pytest.mark.parametrize("visibility", list(FileVisibility))
def test_visibility_requires_actor_and_respects_pinned_denial_even_under_system(
    drive: Any, visibility: FileVisibility,
) -> None:
    row = _proxy_upload(drive, PNG_BYTES)
    outsider = create_user("pinned-visibility-outsider")
    with system_context(reason="test.storage.visibility.actorless"):
        unbound = File.objects.get(pk=row.pk)
        with pytest.raises(PermissionDenied):
            unbound.set_visibility(visibility)
        with pytest.raises(PermissionDenied):
            unbound.with_actor(outsider).set_visibility(visibility)
    with actor_context(drive.alice), pytest.raises(PermissionDenied):
        unbound.with_actor(outsider).set_visibility(visibility)
