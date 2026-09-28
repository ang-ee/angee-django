"""Storage ownership, container defaults, sharing and role inclusion contracts."""

from typing import Any

import pytest
from django.db.models.signals import post_save
from rebac import ObjectRef, RelationshipTuple, SubjectRef, actor_context, system_context, to_subject_ref
from rebac.errors import PermissionDenied
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter

from angee.storage.models import FileVisibility
from tests.conftest import File, Folder, create_platform_admin, create_user
from tests.storage_campaign import relationship_storage as relationship_storage
from tests.test_storage import PNG_BYTES, PNG_SHA256, _proxy_upload
from tests.test_storage import drive as drive

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["drive", "file"])
@pytest.mark.parametrize("write", ["save", "partial", "owner", "owner_id"])
@pytest.mark.parametrize("authorized", [False, True], ids=["editor", "owner"])
def test_owner_column_writes_require_transfer(
    drive: Any, kind: str, write: str, authorized: bool,
) -> None:
    """All ORM update forms enforce transfer independently of ordinary write."""

    editor = create_user("owner-column-editor")
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).grant_record_access("editor", editor)
    row = drive if kind == "drive" else _proxy_upload(drive, PNG_BYTES)
    model = type(row)
    actor = drive.alice if authorized else editor
    with actor_context(actor):
        row = model.objects.get(pk=row.pk)
        assert row.has_access("write")
        assert row.has_access("transfer") is authorized

        def change_owner() -> None:
            if write in {"save", "partial"}:
                row.owner = editor
                row.save(**({"update_fields": ["owner"]} if write == "partial" else {}))
            else:
                model.objects.filter(pk=row.pk).update(**{write: editor.pk if write == "owner_id" else editor})

        if authorized:
            change_owner()
        else:
            with pytest.raises(PermissionDenied):
                change_owner()
    with system_context(reason="test.storage.owner.inspect"):
        fresh = model.objects.get(pk=row.pk)
    assert fresh.owner_id == (editor.pk if authorized else drive.alice.pk)
    assert fresh.created_by_id == drive.alice.pk


@pytest.mark.parametrize("kind", ["drive", "file"])
def test_editor_can_edit_content_but_cannot_transfer_or_gain_owner_delete(drive: Any, kind: str) -> None:
    editor = create_user("ordinary-editor")
    recipient = create_user("transfer-recipient")
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).grant_record_access("editor", editor)
    row = drive if kind == "drive" else _proxy_upload(drive, PNG_BYTES)
    with actor_context(editor):
        row = type(row).objects.get(pk=row.pk)
        field = "name" if kind == "drive" else "title"
        setattr(row, field, "Edited")
        row.save(update_fields=[field])
        with pytest.raises(PermissionDenied):
            row.transfer_ownership(editor)
    with actor_context(drive.alice):
        row.with_actor(drive.alice).transfer_ownership(recipient)
    with actor_context(recipient):
        assert type(row).objects.get(pk=row.pk).has_access("transfer")
        row.with_actor(recipient).transfer_ownership(None)
    with system_context(reason="test.storage.transfer.inspect"):
        row.refresh_from_db()
    assert row.owner_id is None
    assert row.created_by_id == drive.alice.pk
    assert not row.with_actor(recipient).has_access("read")
    assert not row.with_actor(editor).has_access("transfer")
    if kind == "drive":
        assert not row.with_actor(editor).has_access("delete")


@pytest.mark.parametrize("kind", ["drive", "file"])
def test_platform_admin_can_transfer_storage_ownership(drive: Any, kind: str) -> None:
    admin = create_platform_admin("storage-transfer-admin")
    recipient = create_user("admin-transfer-recipient")
    row = drive if kind == "drive" else _proxy_upload(drive, PNG_BYTES)
    with actor_context(admin):
        row.with_actor(admin).transfer_ownership(recipient)
    assert (row.owner_id, row.created_by_id) == (recipient.pk, drive.alice.pk)


def test_protected_release_only_clears_selected_owner_and_never_regrants(drive: Any) -> None:
    author = create_user("released-author")
    other = create_user("retained-owner")
    with system_context(reason="test.storage.release.seed"):
        rows = [
            File.objects.create(
                drive=drive, filename=f"{index}.txt", content_hash=f"{index:064x}",
                owner=owner, created_by=owner, visibility=FileVisibility.RECORD,
            )
            for index, owner in enumerate((author, author, other), start=1)
        ]
    with actor_context(drive.alice):
        assert drive.with_actor(drive.alice).has_access("share")
        assert File.objects.filter(pk__in=[rows[0].pk, rows[2].pk]).release(author) == 1
    with system_context(reason="test.storage.release.inspect"):
        for row in rows:
            row.refresh_from_db()
        rows[0].title = "Retained audit row"
        rows[0].save(update_fields=["title"])
        rows[0].refresh_from_db()
    assert [(row.owner_id, row.created_by_id) for row in rows] == [
        (None, author.pk), (author.pk, author.pk), (other.pk, other.pk),
    ]
    for permission in ("read", "write", "delete"):
        assert not rows[0].with_actor(author).has_access(permission)
        assert rows[1].with_actor(author).has_access(permission)


@pytest.mark.parametrize("owns_items", [False, True])
def test_container_defaults_do_not_change_folder_ownership_or_explicit_ingest_owner(
    drive: Any, owns_items: bool,
) -> None:
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).owns_items = owns_items
        drive.save(update_fields=["owns_items"])
        folder = Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Documents")
        row = File.objects.draft(filename="draft.txt", drive_id=str(drive.sqid))
    assert folder.owner_id is None
    assert row.owner_id == (None if owns_items else drive.alice.pk)
    assert row.created_by_id == drive.alice.pk
    owner = create_user("explicit-ingest-owner")
    inserted: list[tuple[Any, Any]] = []

    def capture(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
        if created:
            inserted.append((instance.owner_id, instance.created_by_id))

    post_save.connect(capture, sender=File)
    try:
        ingested = File.objects.ingest_bytes(
            PNG_BYTES, filename="explicit.png", owner_id=owner.pk, drive_id=str(drive.sqid),
        )
    finally:
        post_save.disconnect(capture, sender=File)
    assert inserted == [(owner.pk, None)]
    assert (ingested.owner_id, ingested.created_by_id) == (owner.pk, None)


def test_external_refresh_preserves_released_ownership_and_empty_attribution(drive: Any) -> None:
    owner = create_user("external-owner")
    values = dict(
        drive=drive, filename="external.png", storage_path="external.png", content_hash=PNG_SHA256,
        size_bytes=len(PNG_BYTES), owner_id=owner.pk,
    )
    row = File.objects.index_external(**values)
    with actor_context(owner):
        row.with_actor(owner).transfer_ownership(None)
    refreshed = File.objects.index_external(**values, metadata={"revision": 2})
    assert refreshed.pk == row.pk
    assert refreshed.owner_id is refreshed.created_by_id is None
    assert refreshed.metadata["revision"] == 2


def test_recursive_role_inclusion_grants_storage_resources_and_revokes_immediately(drive: Any) -> None:
    member = create_user("included-role-member")
    leaf = ObjectRef("storage/role", "leaf")
    middle = ObjectRef("storage/role", "middle")
    root = ObjectRef("storage/role", "storage_admin")
    with system_context(reason="test.storage.role.seed"):
        write_relationships([
            RelationshipTuple(leaf, "member", to_subject_ref(member)),
            RelationshipTuple(middle, "includes", SubjectRef(leaf)),
            RelationshipTuple(root, "includes", SubjectRef(middle)),
        ])
    for row in (drive, drive.backend):
        assert row.with_actor(member).has_access("write")
    with system_context(reason="test.storage.role.revoke"):
        delete_relationships(RelationshipFilter(
            resource_type=root.resource_type, resource_id=root.resource_id, relation="includes",
            subject_type=middle.resource_type, subject_id=middle.resource_id,
        ))
    for row in (drive, drive.backend):
        assert not row.with_actor(member).has_access("read")
