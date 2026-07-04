"""F6 — editable lines + transactional nested write, backend half.

Exercises ``hasura_model_resource(lines=...)`` over the ``linesdemo`` demo
document/line pair: the Hasura-native nested insert writes a parent and its
children atomically (rolling back on a child failure), the authored
``<res>_save`` mutation diff-applies children (create/update/delete by public
id) plus patches the parent in one transaction, the parent write is the REBAC
gate (an actor without write is denied wholesale), and the ``position`` column
round-trips.
"""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.management import call_command
from django.db import connection
from rebac import (
    RelationshipTuple,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from strawberry import auto

from angee.graphql.data.hasura import HasuraLines, hasura_model_resource
from angee.graphql.data.metadata import data_resource_metadata, merge_data_resources
from angee.graphql.node import AngeeNode
from tests.conftest import create_user, execute_schema, result_data
from tests.linesdemo.models import SaleDoc, SaleLine


@strawberry_django.type(SaleLine)
class SaleLineType(AngeeNode):
    """GraphQL projection of one document line."""

    label: auto
    quantity: auto
    position: auto


@strawberry_django.type(SaleDoc)
class SaleDocType(AngeeNode):
    """GraphQL projection of a document with its ordered lines."""

    title: auto
    note: auto

    @strawberry_django.field
    def lines(self) -> list[SaleLineType]:
        return list(self.lines.order_by("position", "pk"))


_LINES = HasuraLines(
    field="lines",
    model=SaleLine,
    node=SaleLineType,
    writable=("label", "quantity", "position"),
)

_RESOURCE = hasura_model_resource(
    SaleDocType,
    model=SaleDoc,
    name="sale_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    writable=["title", "note"],
    lines=_LINES,
    id_column="sqid",
)

_SCHEMA = strawberry.Schema(
    query=_RESOURCE.query,
    mutation=_RESOURCE.mutation,
    types=[SaleDocType, SaleLineType, *_RESOURCE.types],
)


@pytest.fixture()
def linesdemo_tables(transactional_db: Any):
    """Ensure the demo tables exist and the REBAC schema is synced."""

    existing = set(connection.introspection.table_names())
    created = [m for m in (SaleDoc, SaleLine) if m._meta.db_table not in existing]
    if created:
        with connection.schema_editor() as editor:
            for model in created:
                editor.create_model(model)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            for model in (SaleLine, SaleDoc):
                cursor.execute(f"DELETE FROM {connection.ops.quote_name(model._meta.db_table)}")


def _grant_owner(document: SaleDoc, user: Any) -> None:
    """Write the ``owner`` relationship that grants write on a document."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(document),
                relation="owner",
                subject=to_subject_ref(user),
            )
        ]
    )


_INSERT = """
mutation($object: sale_docs_insert_input!) {
  insert_sale_docs_one(object: $object) {
    id
    lines { id label quantity position }
  }
}
"""

_SAVE = """
mutation($pk: ID!, $patch: sale_docs_set_input, $lines: [sale_docs_lines_insert_input!]) {
  sale_docs_save(pk: $pk, patch: $patch, lines: $lines) {
    id
    title
    lines { id label quantity position }
  }
}
"""


def test_nested_insert_writes_parent_and_lines_atomically(linesdemo_tables):
    """One insert mutation persists the document and its child lines."""

    actor = create_user("author")
    result = execute_schema(
        _SCHEMA,
        _INSERT,
        {
            "object": {
                "title": "Quotation",
                "lines": {
                    "data": [
                        {"label": "Widget", "quantity": 2, "position": 0},
                        {"label": "Gadget", "quantity": 5, "position": 1},
                    ]
                },
            }
        },
        user=actor,
    )
    data = result_data(result)
    assert len(data["insert_sale_docs_one"]["lines"]) == 2
    with system_context(reason="test read"):
        doc = SaleDoc.objects.get(title="Quotation")
        rows = list(doc.lines.order_by("position").values_list("label", "quantity", "position"))
    assert rows == [("Widget", 2, 0), ("Gadget", 5, 1)]


def test_nested_insert_rolls_back_parent_on_line_failure(linesdemo_tables):
    """A child validation failure rolls the whole nested insert back."""

    actor = create_user("author")
    result = execute_schema(
        _SCHEMA,
        _INSERT,
        {
            "object": {
                "title": "Doomed",
                "lines": {
                    "data": [
                        {"label": "ok", "quantity": 1, "position": 0},
                        {"label": "x" * 400, "quantity": 1, "position": 1},
                    ]
                },
            }
        },
        user=actor,
    )
    assert result.errors is not None
    with system_context(reason="test read"):
        assert not SaleDoc.objects.filter(title="Doomed").exists()
        assert not SaleLine.objects.filter(label="ok").exists()


def test_save_diffs_lines_create_update_delete_in_one_transaction(linesdemo_tables):
    """``_save`` creates/updates/deletes children and patches the parent atomically."""

    owner = create_user("owner")
    with system_context(reason="seed"):
        doc = SaleDoc.objects.create(title="Order", note="draft")
        keep = SaleLine.objects.create(document=doc, label="Keep", quantity=1, position=0)
        drop = SaleLine.objects.create(document=doc, label="Drop", quantity=9, position=1)
    _grant_owner(doc, owner)

    result = execute_schema(
        _SCHEMA,
        _SAVE,
        {
            "pk": doc.public_id,
            "patch": {"note": "confirmed"},
            "lines": [
                {"id": keep.public_id, "label": "Keep", "quantity": 3, "position": 0},
                {"label": "New", "quantity": 7, "position": 1},
            ],
        },
        user=owner,
    )
    data = result_data(result)
    assert data["sale_docs_save"]["title"] == "Order"

    with system_context(reason="test read"):
        doc.refresh_from_db()
        assert doc.note == "confirmed"
        rows = list(doc.lines.order_by("position").values_list("label", "quantity", "position"))
    # ``keep`` updated to quantity 3, ``drop`` removed, ``New`` created.
    assert rows == [("Keep", 3, 0), ("New", 7, 1)]
    with system_context(reason="test read"):
        assert not SaleLine.objects.filter(pk=drop.pk).exists()


def test_save_without_lines_leaves_children_untouched(linesdemo_tables):
    """Omitting ``lines`` is a parent-only save; the children are left alone."""

    owner = create_user("owner")
    with system_context(reason="seed"):
        doc = SaleDoc.objects.create(title="Order", note="draft")
        SaleLine.objects.create(document=doc, label="Line", quantity=1, position=0)
    _grant_owner(doc, owner)

    result = execute_schema(
        _SCHEMA,
        _SAVE,
        {"pk": doc.public_id, "patch": {"note": "confirmed"}},
        user=owner,
    )
    result_data(result)
    with system_context(reason="test read"):
        doc.refresh_from_db()
        assert doc.note == "confirmed"
        assert doc.lines.count() == 1


def test_save_denies_actor_without_write_on_parent(linesdemo_tables):
    """An actor with no write on the parent is denied — the row is never found."""

    owner = create_user("owner")
    intruder = create_user("intruder")
    with system_context(reason="seed"):
        doc = SaleDoc.objects.create(title="Order")
        line = SaleLine.objects.create(document=doc, label="Line", quantity=1, position=0)
    _grant_owner(doc, owner)

    result = execute_schema(
        _SCHEMA,
        _SAVE,
        {
            "pk": doc.public_id,
            "patch": {"title": "Hijacked"},
            "lines": [{"id": line.public_id, "label": "Tampered", "quantity": 99, "position": 0}],
        },
        user=intruder,
    )
    assert result.errors is not None
    with system_context(reason="test read"):
        doc.refresh_from_db()
        line.refresh_from_db()
    assert doc.title == "Order"
    assert line.label == "Line" and line.quantity == 1


def test_lines_resource_metadata_is_emitted():
    """The resource advertises the editable-lines contract + the save root."""

    merged = merge_data_resources(
        (
            *data_resource_metadata(_RESOURCE.query),
            *data_resource_metadata(_RESOURCE.mutation),
        )
    )
    (resource,) = [m for m in merged if m.model_label == "linesdemo.SaleDoc"]
    assert "save" in resource.capabilities
    assert resource.roots.save_name == "sale_docs_save"
    assert resource.lines is not None
    assert resource.lines.field == "lines"
    assert resource.lines.model_label == "linesdemo.SaleLine"
    assert resource.lines.position_field == "position"
    line_field_names = {field.name for field in resource.lines.fields}
    assert {"label", "quantity", "position"} <= line_field_names
    # The parent create fields must not leak the nested lines envelope.
    assert "lines" not in resource.create_fields
