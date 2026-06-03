"""GraphQL delete preview tree tests."""

from __future__ import annotations

import pytest
import strawberry
import strawberry_django
from django.db import connection, models
from strawberry import relay

from angee.base.graphql import crud


@pytest.mark.django_db(transaction=True)
def test_delete_note_dry_run_returns_tree_and_confirm_deletes() -> None:
    """A delete mutation can preview a cascade tree without deleting rows."""

    class Note(models.Model):
        """Concrete note model used for delete preview tree tests."""

        title = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

        def __str__(self) -> str:
            """Return the note title for preview display labels."""

            return self.title

    class NoteChild(models.Model):
        """Cascade child used to exercise grouped preview leaves."""

        note = models.ForeignKey(Note, on_delete=models.CASCADE)
        name = models.CharField(max_length=32)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

        def __str__(self) -> str:
            """Return the child name for preview display labels."""

            return self.name

    @strawberry_django.type(Note)
    class NoteType:
        """GraphQL type for the test note model."""

        title: str

    @strawberry.type
    class Query:
        """Query root required by Strawberry schemas."""

        ok: bool = True

    schema = strawberry.Schema(
        query=Query,
        mutation=crud(NoteType, delete=True, name="note"),
        types=[NoteType],
    )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(Note)
        schema_editor.create_model(NoteChild)
    try:
        note = Note.objects.create(title="Draft")
        NoteChild.objects.bulk_create(NoteChild(note=note, name=f"child-{index:02d}") for index in range(52))

        dry_run = schema.execute_sync(
            """
            mutation DeleteNote($id: ID!) {
              deleteNote(id: $id, confirm: false) {
                root {
                  label
                  objectLabel
                  objectId
                  children {
                    label
                    objectLabel
                    objectId
                    children {
                      label
                      objectLabel
                      objectId
                    }
                  }
                }
              }
            }
            """,
            variable_values={"id": relay.to_base64("NoteType", note.pk)},
        )

        assert dry_run.errors is None
        assert Note.objects.filter(pk=note.pk).exists()
        root = dry_run.data["deleteNote"]["root"]
        assert root["label"] == "note"
        assert root["objectLabel"] == "Draft"
        assert root["objectId"] == str(note.pk)
        group = root["children"][0]
        assert group["objectLabel"] == "52 note childs"
        assert group["children"][-1]["objectLabel"] == "… and 2 more"

        confirmed = schema.execute_sync(
            """
            mutation DeleteNote($id: ID!) {
              deleteNote(id: $id) {
                root {
                  objectId
                }
              }
            }
            """,
            variable_values={"id": relay.to_base64("NoteType", note.pk)},
        )

        assert confirmed.errors is None
        assert not Note.objects.filter(pk=note.pk).exists()
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(NoteChild)
            schema_editor.delete_model(Note)
