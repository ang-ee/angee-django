"""Storage placement contracts using the existing project test composition."""

from io import BytesIO
from typing import Any

import pytest
from rebac import actor_context, system_context

from angee.projects.testing.models import Project, ProjectBinding
from tests.conftest import Drive, File, Folder, create_user
from tests.storage_campaign import relationship_storage as relationship_storage
from tests.test_project_access import project_access_schema as project_access_schema
from tests.test_storage import PNG_BYTES, PNG_SHA256
from tests.test_storage import drive as drive

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("project_access_schema")]


def test_shared_drive_folder_does_not_narrow_files_to_project_readers(drive: Any) -> None:
    reader, outsider = create_user("project-reader"), create_user("drive-reader")
    with actor_context(drive.alice):
        project = Project.objects.create(title="Folder project")
        folder = Folder.objects.create_in_drive(drive_id=str(drive.sqid), name="Project files")
        ProjectBinding.objects.bind(project=project, target=folder)
        project.grant_record_access("reader", reader)
        drive.with_actor(drive.alice).grant_record_access("viewer", outsider)
        row = File.objects.draft(filename="shared.txt", drive_id=str(drive.sqid), folder_id=str(folder.sqid))
    assert row.with_actor(reader).has_access("read")
    assert row.with_actor(outsider).has_access("read")


def test_bound_owning_drive_revokes_upload_access_with_project_membership(drive: Any) -> None:
    reader, editor, outsider = (create_user(name) for name in ("bound-reader", "bound-editor", "bound-outsider"))
    with actor_context(drive.alice):
        drive.with_actor(drive.alice).owns_items = True
        drive.save(update_fields=["owns_items"])
        project = Project.objects.create(title="Bound project")
        ProjectBinding.objects.bind(project=project, target=drive)
        project.grant_record_access("reader", reader)
        project.grant_record_access("editor", editor)
    with actor_context(editor):
        row = File.objects.draft(filename="bound.png", drive_id=str(drive.sqid))
        assert row.owner_id is None
        File.objects.for_upload_token(row.issue_upload_token()).receive_bytes(BytesIO(PNG_BYTES))
        row.finalize(expected_hash=PNG_SHA256)
    assert row.with_actor(reader).has_access("read")
    assert not row.with_actor(outsider).has_access("read")
    with actor_context(drive.alice):
        project.with_actor(drive.alice).revoke_record_access("reader", reader)
        project.revoke_record_access("editor", editor)
    for actor in (reader, editor):
        assert not row.with_actor(actor).has_access("read")
    assert row.created_by_id == editor.pk


def test_identical_bytes_in_separate_bound_drives_keep_distinct_rows(drive: Any) -> None:
    with system_context(reason="test.storage.placement.seed"):
        second = Drive.objects.create(
            backend=drive.backend, name="Second", slug="second", prefix="second", owner=drive.alice,
        )
    rows = []
    with actor_context(drive.alice):
        for index, target in enumerate((drive, second)):
            project = Project.objects.create(title=f"Project {index}")
            ProjectBinding.objects.bind(project=project, target=target.with_actor(drive.alice))
            rows.append(File.objects.ingest_bytes(
                PNG_BYTES, filename="same.png", drive_id=str(target.sqid), owner_id=drive.alice.pk,
            ))
    assert rows[0].pk != rows[1].pk
    assert rows[0].storage_path != rows[1].storage_path
    assert rows[0].content_hash == rows[1].content_hash == PNG_SHA256
