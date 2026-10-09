"""Tests for the placeable ``tags`` relation list owners compose from ``TaggedNode``.

Storage's ``File`` stands in for every picked model: it reaches the field
through a console ``type_extensions`` donor, the same ``TaggedNode`` the other
owners compose onto their console node classes. Parties carry tags at the party
for both of its kinds. The structural test pins the owners whose schema modules
import in the bare test runtime; the projects console nodes are pinned beside
the work declarations they already need (``tests/test_work_task_access.py``).
Every taggable type's relation comes from its owner's fragment, so the tests
compose them (``composed_permissions``).
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

import tests.test_storage  # noqa: F401 -- register the fixture model graph before database setup
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging.testing.models import Organization, Party
from angee.storage import schema as storage_schema
from angee.tags.schema import TaggedNode
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import File, SchemaAddon, create_user, execute_schema, result_data
from tests.test_storage import drive as drive

# Import after the concrete test models are registered; the source schema resolves
# the runtime models through Django's app registry.
parties_schema = importlib.import_module("angee.parties.schema")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_permissions")]

_PICKED_CONSOLE_NODES = (
    ("angee.messaging.schema", "ThreadType"),
    ("angee.messaging.schema", "MessageType"),
    ("angee.knowledge.schema", "PageTags"),
    ("angee.storage.schema", "FileTags"),
    ("angee.parties.schema", "PartyTags"),
    ("angee.parties.schema", "PersonTags"),
    ("angee.parties.schema", "OrganizationTags"),
)


def _addon_schemas(module: ModuleType, name: str) -> GraphQLSchemas:
    """Return one addon's own ``name`` schema as a throwaway addon (reset after each test)."""

    return GraphQLSchemas([SchemaAddon({
        name: {key: tuple(module.schemas[name].get(key, ())) for key in SCHEMA_PART_KEYS},
    })])


def _storage_schemas(name: str) -> GraphQLSchemas:
    """Return storage's own ``name`` schema as a throwaway addon."""

    return _addon_schemas(storage_schema, name)


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
    public = _addon_schemas(parties_schema, "public").build("public")._schema
    console = _addon_schemas(parties_schema, "console").build("console")._schema
    for name in ("PartyType", "PersonType", "OrganizationType"):
        assert "tags" not in public.get_type(name).fields
        assert str(console.get_type(name).fields["tags"].type) == "[TagType!]!"


def test_metadata_projects_tags_as_a_writable_relation_list() -> None:
    """The field is a ``list`` relation to ``tags.Tag`` the resource's set input writes, with no widget of its own."""

    metadata = next(item for item in _storage_schemas("console").resources("console") if item.model is File)
    field = next(field for field in metadata.fields if field.name == "tags")

    assert (field.kind, field.relation_model_label, field.widget) == ("list", "tags.Tag", None)
    # Files take no inserts through this resource, so only their set input carries tags.
    assert (field.readable, field.creatable, field.updatable) == (True, False, True)
    assert "tags" not in metadata.create_fields
    assert "tags" in metadata.update_fields


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


_RETAG = """mutation Retag($id: String!, $tags: [ID!]) {
  update_files_by_pk(pk_columns: {id: $id}, _set: {tags: $tags}) { id tags { name } }
}"""
_RENAME = """mutation Rename($id: String!) {
  update_files_by_pk(pk_columns: {id: $id}, _set: {title: "Renamed"}) { id tags { name } }
}"""


def _saved_tag_names(row: Any) -> list[str]:
    with system_context(reason="tags field save check"):
        return [edge.tag.name for edge in TagAssignment.objects.for_record(row).by_tag().select_related("tag")]


def test_a_record_save_writes_its_tags_and_an_omitted_value_leaves_them(drive: Any) -> None:
    """The set input's ``tags`` becomes the row's tags in its save; a save without it changes none."""

    files, tags = _tagged_files(drive, 1)
    with system_context(reason="tags field save seed"):
        billing = Tag.objects.create(name="Billing", color="")
    schema = _storage_schemas("console").build("console")
    variables = {"id": str(files[0].sqid), "tags": [str(tags[0].sqid), str(billing.sqid)]}

    saved = result_data(execute_schema(schema, _RETAG, variables, user=drive.alice))
    assert saved["update_files_by_pk"]["tags"] == [{"name": "Alpha"}, {"name": "Billing"}]
    assert _saved_tag_names(files[0]) == ["Alpha", "Billing"]

    renamed = result_data(execute_schema(schema, _RENAME, {"id": str(files[0].sqid)}, user=drive.alice))
    assert renamed["update_files_by_pk"]["tags"] == [{"name": "Alpha"}, {"name": "Billing"}]

    cleared = result_data(execute_schema(schema, _RETAG, {"id": str(files[0].sqid), "tags": []}, user=drive.alice))
    assert cleared["update_files_by_pk"]["tags"] == []
    assert _saved_tag_names(files[0]) == []


def test_only_a_record_writer_saves_its_tags(drive: Any) -> None:
    """Someone who cannot write the row cannot change its tags through its save."""

    files, tags = _tagged_files(drive, 1)
    outsider = create_user("tags-field-retag-outsider")
    schema = _storage_schemas("console").build("console")
    result = execute_schema(schema, _RETAG, {"id": str(files[0].sqid), "tags": []}, user=outsider)

    assert result.errors or result.data == {"update_files_by_pk": None}
    assert _saved_tag_names(files[0]) == [tag.name for tag in tags]


def test_an_unknown_tag_rolls_the_whole_save_back(drive: Any) -> None:
    """A tag that does not resolve fails the save, and the row and its tags stay as they were."""

    files, tags = _tagged_files(drive, 1)
    schema = _storage_schemas("console").build("console")
    document = """mutation Retag($id: String!, $tags: [ID!]) {
      update_files_by_pk(pk_columns: {id: $id}, _set: {title: "Renamed", tags: $tags}) { id }
    }"""
    result = execute_schema(schema, document, {"id": str(files[0].sqid), "tags": ["tag_missing"]}, user=drive.alice)

    assert result.errors
    with system_context(reason="tags field rollback check"):
        assert File.objects.get(pk=files[0].pk).title != "Renamed"
    assert _saved_tag_names(files[0]) == [tag.name for tag in tags]


_CREATE_PERSON = """mutation CreatePerson($tags: [ID!]) {
  insert_people_one(object: {display_name: "Ada", tags: $tags}) { id tags { name } }
}"""
_RETAG_ORGANIZATION = """mutation RetagOrganization($id: String!, $tags: [ID!]) {
  update_organizations_by_pk(pk_columns: {id: $id}, _set: {tags: $tags}) { id tags { name } }
}"""


def test_a_party_saves_its_tags_with_either_kinds_form() -> None:
    """A person's create and an organization's update save tags, which every kind reads at the party."""

    owner = create_user("tags-party-form-owner")
    with system_context(reason="tags party form seed"):
        vip, wholesale = Tag.objects.create(name="VIP", color=""), Tag.objects.create(name="Wholesale", color="")
    with actor_context(owner):
        organization = Organization.objects.create(display_name="Acme")
    schema = _addon_schemas(parties_schema, "console").build("console")

    created = result_data(execute_schema(schema, _CREATE_PERSON, {"tags": [str(vip.sqid)]}, user=owner))
    assert created["insert_people_one"]["tags"] == [{"name": "VIP"}]
    retagged = result_data(execute_schema(
        schema, _RETAG_ORGANIZATION, {"id": str(organization.sqid), "tags": [str(wholesale.sqid), str(vip.sqid)]},
        user=owner,
    ))
    assert retagged["update_organizations_by_pk"]["tags"] == [{"name": "VIP"}, {"name": "Wholesale"}]

    with system_context(reason="tags party form check"):
        assert {edge.content_type.model_class() for edge in TagAssignment.objects.all()} == {Party}
    listed = result_data(execute_schema(
        schema, "{ parties(order_by: [{display_name: asc}]) { display_name tags { name } } }", user=owner,
    ))
    assert listed["parties"] == [
        {"display_name": "Acme", "tags": [{"name": "VIP"}, {"name": "Wholesale"}]},
        {"display_name": "Ada", "tags": [{"name": "VIP"}]},
    ]
    people = result_data(execute_schema(schema, "{ people { display_name tags { name } } }", user=owner))
    assert people["people"] == [{"display_name": "Ada", "tags": [{"name": "VIP"}]}]
