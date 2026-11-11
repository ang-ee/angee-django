"""Storage ownership release, container ownership defaults and external refresh contracts."""

from typing import Any

import pytest
from django.db.models.signals import post_save
from rebac import actor_context, system_context

from angee.storage.models import FileVisibility
from tests.conftest import File, Folder, create_user
from tests.test_storage import PNG_BYTES, PNG_SHA256
from tests.test_storage import drive as drive


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
        ingested, _created = File.objects.ingest_bytes(
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


