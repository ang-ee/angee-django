"""Rows-present proof for the ``attachment.bin`` display-name backfill."""

from types import SimpleNamespace

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.storage.runtime_migrations.rename_attachment_bin import applies, rename_attachment_bin


def _historical_state() -> ProjectState:
    """Build the minimal File/MimeType shape the backfill reads and writes."""

    state = ProjectState()
    state.add_model(
        ModelState(
            "storage",
            "MimeType",
            [
                ("id", models.AutoField(primary_key=True)),
                ("mime_type", models.CharField(max_length=200)),
            ],
            options={"db_table": "test_historical_mimetype"},
        )
    )
    state.add_model(
        ModelState(
            "storage",
            "File",
            [
                ("id", models.AutoField(primary_key=True)),
                ("filename", models.CharField(max_length=512)),
                ("storage_path", models.CharField(max_length=2048, default="")),
                ("mime_type", models.ForeignKey("storage.MimeType", null=True, on_delete=models.SET_NULL)),
            ],
            options={"db_table": "test_historical_file"},
        )
    )
    return state


@pytest.mark.django_db(transaction=True)
def test_rename_attachment_bin_derives_from_mime_and_is_idempotent() -> None:
    """Each ``attachment.bin`` file takes a MIME-derived name; the blob key stays."""

    state = _historical_state()
    mime_model = state.apps.get_model("storage", "MimeType")
    file_model = state.apps.get_model("storage", "File")
    with connection.schema_editor() as editor:
        editor.create_model(mime_model)
        editor.create_model(file_model)
    try:
        jpeg = mime_model.objects.create(mime_type="image/jpeg")
        octet = mime_model.objects.create(mime_type="application/octet-stream")
        renamed = file_model.objects.create(
            filename="attachment.bin", storage_path="ab/cd/hash/attachment.bin", mime_type=jpeg
        )
        opaque = file_model.objects.create(
            filename="attachment.bin", storage_path="ef/gh/hash/attachment.bin", mime_type=octet
        )
        untyped = file_model.objects.create(
            filename="attachment.bin", storage_path="ij/kl/hash/attachment.bin", mime_type=None
        )
        kept = file_model.objects.create(filename="brief.txt", storage_path="mn/op/hash/brief.txt", mime_type=jpeg)

        editor_ns = SimpleNamespace(connection=connection)
        rename_attachment_bin(state.apps, editor_ns)
        rename_attachment_bin(state.apps, editor_ns)  # second pass is a no-op

        for row in (renamed, opaque, untyped, kept):
            row.refresh_from_db()
        # A known MIME becomes ``attachment{ext}``; the content-addressed key is untouched.
        assert renamed.filename == "attachment.jpg"
        assert renamed.storage_path == "ab/cd/hash/attachment.bin"
        # The generic catch-all and an unknown MIME legitimately stay ``.bin``.
        assert opaque.filename == "attachment.bin"
        assert untyped.filename == "attachment.bin"
        # A file that already carried a real name is never touched.
        assert kept.filename == "brief.txt"
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(file_model)
            editor.delete_model(mime_model)


def test_applies_requires_the_file_name_and_mime_shape() -> None:
    """The data fix is applicable exactly when its target File shape exists."""

    assert applies(ProjectState()) is False
    assert applies(_historical_state()) is True
