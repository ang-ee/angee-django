"""Rows-present proof for the nameless-attachment display-name backfill."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.messaging.runtime_migrations.name_unnamed_attachment_parts import (
    applies,
    name_unnamed_attachment_parts,
)


def _historical_state() -> ProjectState:
    """Build the minimal Message/Part/File shape the backfill reads and writes."""

    state = ProjectState()
    state.add_model(
        ModelState(
            "messaging",
            "Message",
            [
                ("id", models.AutoField(primary_key=True)),
                ("external_id", models.CharField(max_length=998)),
                ("message_type", models.CharField(max_length=32)),
                ("sent_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(null=True)),
            ],
            options={"db_table": "test_hist_message"},
        )
    )
    state.add_model(
        ModelState(
            "storage",
            "File",
            [
                ("id", models.AutoField(primary_key=True)),
                ("filename", models.CharField(max_length=512)),
            ],
            options={"db_table": "test_hist_file"},
        )
    )
    state.add_model(
        ModelState(
            "messaging",
            "Part",
            [
                ("id", models.AutoField(primary_key=True)),
                ("name", models.CharField(max_length=512, default="")),
                ("cid", models.CharField(max_length=4096, default="")),
                ("type", models.CharField(max_length=128, default="text/plain")),
                ("position", models.PositiveIntegerField(default=0)),
                ("message", models.ForeignKey("messaging.Message", on_delete=models.CASCADE)),
                ("file", models.ForeignKey("storage.File", null=True, on_delete=models.SET_NULL)),
            ],
            options={"db_table": "test_hist_part"},
        )
    )
    return state


@pytest.mark.django_db(transaction=True)
def test_backfill_names_parts_and_files_and_is_idempotent() -> None:
    """Nameless parts and fallback-named files converge on the frozen rule; the blob is untouched."""

    state = _historical_state()
    message_model = state.apps.get_model("messaging", "Message")
    file_model = state.apps.get_model("storage", "File")
    part_model = state.apps.get_model("messaging", "Part")
    with connection.schema_editor() as editor:
        editor.create_model(message_model)
        editor.create_model(file_model)
        editor.create_model(part_model)
    try:
        early = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late = datetime(2026, 2, 1, tzinfo=timezone.utc)

        chat = message_model.objects.create(
            external_id="4917@s.whatsapp.net/AAA", message_type="chat", sent_at=early, created_at=early
        )
        multi = message_model.objects.create(
            external_id="group@g.us/BBB", message_type="chat", sent_at=late, created_at=late
        )
        email = message_model.objects.create(
            external_id="mid@host", message_type="email", sent_at=early, created_at=early
        )

        # A chat part, a two-nameless-part chat message (index suffix), an email inline
        # part (cid), an email attachment without a cid (fallback), and an already-named
        # part that the fix must never touch.
        chat_file = file_model.objects.create(filename="attachment.jpg")
        multi_file_a = file_model.objects.create(filename="attachment.jpg")
        multi_file_b = file_model.objects.create(filename="attachment.png")
        inline_file = file_model.objects.create(filename="attachment.png")
        opaque_file = file_model.objects.create(filename="attachment.bin")
        named_file = file_model.objects.create(filename="attachment.pdf")
        real_name_file = file_model.objects.create(filename="brief.txt")

        chat_part = part_model.objects.create(
            name="", cid="", type="image/jpeg", position=0, message=chat, file=chat_file
        )
        multi_a = part_model.objects.create(
            name="", cid="", type="image/jpeg", position=0, message=multi, file=multi_file_a
        )
        multi_b = part_model.objects.create(
            name="", cid="", type="image/png", position=1, message=multi, file=multi_file_b
        )
        inline_part = part_model.objects.create(
            name="", cid="<hero@host>", type="image/png", position=0, message=email, file=inline_file
        )
        opaque_part = part_model.objects.create(
            name="", cid="", type="application/octet-stream", position=1, message=email, file=opaque_file
        )
        # A file left under the fallback whose earliest part already carries a real
        # name takes that name, not a synthesized one.
        named_part = part_model.objects.create(
            name="report.pdf", cid="", type="application/pdf", position=2, message=email, file=named_file
        )
        # A file that already carries a real display name is never considered.
        kept_part = part_model.objects.create(
            name="", cid="", type="text/plain", position=3, message=email, file=real_name_file
        )

        editor_ns = SimpleNamespace(connection=connection)
        name_unnamed_attachment_parts(state.apps, editor_ns)
        name_unnamed_attachment_parts(state.apps, editor_ns)  # second pass is a no-op

        for row in (chat_part, multi_a, multi_b, inline_part, opaque_part, named_part, kept_part):
            row.refresh_from_db()
        for row in (chat_file, multi_file_a, multi_file_b, inline_file, opaque_file, named_file, real_name_file):
            row.refresh_from_db()

        # Pass 1: every nameless part now carries its derived name.
        assert chat_part.name == "AAA.jpg"
        assert multi_a.name == "BBB.jpg"
        assert multi_b.name == "BBB-1.png"  # the second nameless part of one message
        assert inline_part.name == "inline-herohost.png"
        assert opaque_part.name == "attachment.bin"  # no chat, no cid -> shared fallback
        assert named_part.name == "report.pdf"  # already named -> untouched
        assert kept_part.name == "attachment.txt"  # nameless email attachment -> fallback

        # Pass 2: each fallback-named file converges on its earliest part's name.
        assert chat_file.filename == "AAA.jpg"
        assert multi_file_a.filename == "BBB.jpg"
        assert multi_file_b.filename == "BBB-1.png"
        assert inline_file.filename == "inline-herohost.png"
        assert opaque_file.filename == "attachment.bin"  # legitimately stays on the fallback
        assert named_file.filename == "report.pdf"  # takes the earliest part's real name
        assert real_name_file.filename == "brief.txt"  # a real name is never reconsidered
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(part_model)
            editor.delete_model(file_model)
            editor.delete_model(message_model)


def test_applies_requires_the_part_message_and_file_shape() -> None:
    """The data fix is applicable exactly when its Part/Message/File shape exists."""

    assert applies(ProjectState()) is False
    assert applies(_historical_state()) is True
