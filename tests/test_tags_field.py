"""Tests for the placeable ``tags`` relation list owners compose from ``TaggedNode``.

Storage's ``File`` stands in for every picked model: it reaches the field
through a console ``type_extensions`` donor, the same ``TaggedNode`` the other
owners compose onto their console node classes. The structural test pins the
owners whose schema modules import in the bare test runtime; the projects
console nodes are pinned beside the work declarations they already need
(``tests/test_work_task_access.py``).
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

import tests.test_storage  # noqa: F401 -- register the fixture model graph before database setup
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.storage import schema as storage_schema
from angee.tags.schema import TAGS_WIDGET, TaggedNode
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import File, SchemaAddon, create_user, execute_schema, result_data
from tests.test_storage import drive as drive

pytestmark = pytest.mark.django_db(transaction=True)

_PICKED_CONSOLE_NODES = (
    ("angee.messaging.schema", "ThreadType"),
    ("angee.messaging.schema", "MessageType"),
    ("angee.knowledge.schema", "PageTags"),
    ("angee.storage.schema", "FileTags"),
)


def _storage_schemas(name: str) -> GraphQLSchemas:
    """Return storage's own ``name`` schema as a throwaway addon (reset after each test)."""

    return GraphQLSchemas([SchemaAddon({
        name: {key: tuple(storage_schema.schemas[name].get(key, ())) for key in SCHEMA_PART_KEYS},
    })])


def _tagged_files(drive: Any, count: int) -> tuple[list[Any], list[Any]]:
    """Seed ``count`` files on ``drive`` carrying the same two tags, attached as the drive owner."""

    with system_context(reason="tags field seed"):
        tags = [Tag.objects.create(name=name, color=color) for name, color in (("Zoned", "#00f"), ("Alpha", "#f00"))]
        files = [
            File.objects.create(
                drive=drive, created_by=drive.alice, filename=f"file-{index:02}.txt", content_hash=f"{index:064x}",
            )
            for index in range(count)
        ]
    with actor_context(drive.alice):
        for row in files:
            TagAssignment.objects.attach("storage/file", row.sqid, [tag.sqid for tag in tags])
    return files, sorted(tags, key=lambda tag: tag.name)


@pytest.mark.parametrize(("module", "name"), _PICKED_CONSOLE_NODES)
def test_each_picked_console_node_composes_tagged_node(module: str, name: str) -> None:
    """Owners compose ``TaggedNode`` once per console node, or through a console donor."""

    node = getattr(importlib.import_module(module), name)
    assert issubclass(node, TaggedNode)
    assert "tags" in {field.python_name for field in node.__strawberry_definition__.fields}


def test_shared_public_nodes_stay_untagged() -> None:
    """A node shared with the public schema reads tags on the console only."""

    public = _storage_schemas("public").build("public")._schema
    console = _storage_schemas("console").build("console")._schema
    assert "tags" not in public.get_type("FileType").fields
    assert str(console.get_type("FileType").fields["tags"].type) == "[TagType!]!"


def test_metadata_projects_tags_as_a_read_only_relation_list_with_the_tags_widget() -> None:
    """The field reads as a ``list`` relation to ``tags.Tag`` that no insert or set input writes."""

    metadata = next(item for item in _storage_schemas("console").resources("console") if item.model is File)
    field = next(field for field in metadata.fields if field.name == "tags")

    assert (field.kind, field.relation_model_label, field.widget) == ("list", "tags.Tag", TAGS_WIDGET)
    assert (field.readable, field.creatable, field.updatable) == (True, False, False)
    assert "tags" not in metadata.create_fields
    assert "tags" not in metadata.update_fields


def test_a_reader_lists_each_rows_tags_in_one_batch_per_page(drive: Any) -> None:
    """A page of rows resolves every row's tags with the same query count at one and 25 rows."""

    files, tags = _tagged_files(drive, 25)
    expected = [
        {"id": str(row.sqid), "tags": [{"id": str(tag.sqid), "name": tag.name, "color": tag.color} for tag in tags]}
        for row in files
    ]
    schema = _storage_schemas("console").build("console")
    query = """
        query FileTags($limit: Int!) {
          files(limit: $limit, order_by: [{filename: asc}]) { id tags { id name color } }
        }
    """
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size}, user=drive.alice))["files"]
        assert rows == expected[:size]
        counts.append(len(captured))
    assert counts[0] == counts[1], f"Tag queries grew with the page: {counts}"

    detail = "query FileTags($id: String!) { files_by_pk(id: $id) { id tags { id name color } } }"
    assert result_data(execute_schema(schema, detail, {"id": str(files[0].sqid)}, user=drive.alice)) == {
        "files_by_pk": expected[0],
    }


def test_tags_follow_the_record_readers_scope(drive: Any) -> None:
    """An outsider who cannot read the file sees neither the row nor its tags."""

    files, _tags = _tagged_files(drive, 1)
    outsider = create_user("tags-field-outsider")
    schema = _storage_schemas("console").build("console")
    detail = "query FileTags($id: String!) { files_by_pk(id: $id) { id tags { id } } }"

    assert result_data(execute_schema(schema, detail, {"id": str(files[0].sqid)}, user=outsider)) == {
        "files_by_pk": None,
    }


def test_an_unoptimized_row_resolves_its_tags_with_one_scoped_read(drive: Any) -> None:
    """Outside the optimizer a row still answers with one actor-scoped edge read."""

    files, tags = _tagged_files(drive, 1)
    field = next(field for field in TaggedNode.__strawberry_definition__.fields if field.python_name == "tags")
    assert field.base_resolver is not None
    with actor_context(drive.alice):
        row = File.objects.get(pk=files[0].pk)
        with CaptureQueriesContext(connection) as captured:
            resolved = field.base_resolver.wrapped_func(row)
    assert [tag.pk for tag in resolved] == [tag.pk for tag in tags]
    edge_table = TagAssignment._meta.db_table
    assert sum(edge_table in query["sql"] for query in captured.captured_queries) == 1
