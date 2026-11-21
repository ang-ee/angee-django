"""GraphQL delete preview tree tests."""

from __future__ import annotations

import pytest
import strawberry
import strawberry_django
from django.db import models

from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.testing.documents import execute_schema
from tests.conftest import addon_schema
from tests.tables import model_tables


@pytest.mark.django_db(transaction=True)
def test_generated_delete_dry_run_returns_tree_and_confirm_deletes() -> None:
    """A plain deletable resource generates a preview and confirms the same cascade."""

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
        """The row projection; deletion is declared only on the resource."""

        id: strawberry.ID
        title: strawberry.auto

    resource = hasura_model_resource(
        NoteType, model=Note, name="notes",
        filterable=["id", "title"], sortable=["title"], aggregatable=["id"],
        insert=False, update=False, public_id_field="id",
    )
    schema = addon_schema({"public": {
        "query": [resource.query], "mutation": [resource.mutation], "types": resource.types,
    }}, "public")
    assert "notes_delete(id: ID!, confirm: Boolean = false): DeletePreview!" in schema.as_str()
    metadata = schema.angee_resources[0]
    assert metadata.roots.delete_preview_name == "notes_delete"
    assert metadata.type_names.delete_payload == "DeletePreview"
    assert "deletePreview" in metadata.capabilities
    assert metadata.as_wire(schema_name="public")["roots"]["deletePreview"] == "notes_delete"

    with model_tables((Note, NoteChild)):
        note = Note.objects.create(title="Draft")
        NoteChild.objects.bulk_create(NoteChild(note=note, name=f"child-{index:02d}") for index in range(52))

        dry_run = execute_schema(
            schema,
            """
            mutation DeleteNote($id: ID!) {
              notes_delete(id: $id, confirm: false) {
                root {
                  label
                  object_label
                  object_id
                  children {
                    label
                    object_label
                    object_id
                    children {
                      label
                      object_label
                      object_id
                    }
                  }
                }
              }
            }
            """,
            {"id": str(note.pk)},
        )

        assert dry_run.errors is None
        assert Note.objects.filter(pk=note.pk).exists()
        root = dry_run.data["notes_delete"]["root"]
        assert root["label"] == "note"
        assert root["object_label"] == "Draft"
        assert root["object_id"] == str(note.pk)
        group = root["children"][0]
        assert group["object_label"] == "52 note childs"
        assert group["children"][-1]["object_label"] == "… and 2 more"

        default_preview = execute_schema(
            schema,
            """
            mutation DeleteNote($id: ID!) {
              notes_delete(id: $id) {
                root {
                  object_id
                }
              }
            }
            """,
            {"id": str(note.pk)},
        )

        assert default_preview.errors is None
        assert Note.objects.filter(pk=note.pk).exists()

        null_preview = execute_schema(
            schema,
            """
            mutation DeleteNote($id: ID!) {
              notes_delete(id: $id, confirm: null) {
                total_deleted_count has_blockers
              }
            }
            """,
            {"id": str(note.pk)},
        )
        assert null_preview.errors is None
        assert null_preview.data["notes_delete"] == {"total_deleted_count": 53, "has_blockers": False}
        assert Note.objects.filter(pk=note.pk).exists()
        assert NoteChild.objects.filter(note_id=note.pk).count() == 52

        confirmed = execute_schema(
            schema,
            """
            mutation DeleteNote($id: ID!) {
              notes_delete(id: $id, confirm: true) {
                root {
                  object_id
                }
              }
            }
            """,
            {"id": str(note.pk)},
        )

        assert confirmed.errors is None
        assert not Note.objects.filter(pk=note.pk).exists()
        assert not NoteChild.objects.filter(note_id=note.pk).exists()
