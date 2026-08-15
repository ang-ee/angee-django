"""Tests for the MCP GraphQL tool compiler — nested-projection (depth ≤ 2) support.

Builds a tiny Strawberry schema shaped like the knowledge ``read_page`` projection
(a nullable nested object, a nested list, a list of objects) and drives the compiler
in :mod:`angee.mcp.graphql` directly, so the assertions cover the document rendering,
output schema, and row projection without standing up the full discovery schema.
"""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
from django.core.exceptions import ImproperlyConfigured

from angee.graphql.schema import GraphQLSchemas
from angee.mcp import graphql as mcp_graphql
from angee.mcp.graphql import GraphQLTool, _compile
from tests.conftest import SchemaAddon


@strawberry.type
class AnchorT:
    """A third object level — only reachable by an (illegal) depth-3 projection."""

    href: str


@strawberry.type
class OutlineEntryT:
    level: int
    text: str
    slug: str
    anchor: AnchorT | None


@strawberry.type
class MarkdownT:
    body: str
    body_hash: str
    outline: list[OutlineEntryT]


@strawberry.type
class BacklinkT:
    page: strawberry.ID
    title: str
    display_text: str


@strawberry.type
class PageT:
    id: strawberry.ID
    title: str
    kind: str
    markdown: MarkdownT | None
    backlinks: list[BacklinkT]


@strawberry.type
class PageQuery:
    @strawberry.field
    def page(self, id: strawberry.ID) -> PageT | None:
        """Stub root field — only its signature/return type is introspected."""

        del id
        return None


@pytest.fixture
def tiny_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``GraphQLSchemas.from_discovery`` at a one-bucket schema of the types above."""

    schemas = GraphQLSchemas([SchemaAddon({"public": {"query": (PageQuery,)}})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))


_NESTED_FIELDS = (
    "sqid",
    "title",
    "kind",
    ("markdown", ("body", "body_hash", ("outline", ("level", "text", "slug")))),
    ("backlinks", ("page", "title", "display_text")),
)


def _read_page(fields: tuple[Any, ...] = _NESTED_FIELDS) -> GraphQLTool:
    return GraphQLTool(
        operation="page",
        name="read_page",
        fields=fields,
        id_arg="id",
        description="Read one page by sqid.",
    )


def test_flat_spec_still_compiles(tiny_schema: None) -> None:
    """A scalar-only spec keeps the flat behavior: a flat selection and flat projection."""

    compiled = _compile(_read_page(fields=("sqid", "title", "kind")))

    assert compiled.document == "query ($id: ID!) { page(id: $id) { id title kind } }"
    assert compiled._project({"id": "P1", "title": "Hi", "kind": "note"}) == {
        "sqid": "P1",
        "title": "Hi",
        "kind": "note",
    }
    assert compiled.output_schema["properties"]["sqid"] == {"type": "string"}


def test_nested_spec_compiles_to_a_nested_document(tiny_schema: None) -> None:
    """A depth-2 spec renders ``wire { children }`` with the schema's own wire names."""

    compiled = _compile(_read_page())

    # Angee builds schemas with a snake_case name converter (``hasura_config``), so the
    # wire names the compiler emits stay snake_case — children resolve their own wire.
    assert compiled.document == (
        "query ($id: ID!) { page(id: $id) { "
        "id title kind "
        "markdown { body body_hash outline { level text slug } } "
        "backlinks { page title display_text } "
        "} }"
    )


def test_nested_output_schema_describes_objects_and_arrays(tiny_schema: None) -> None:
    """The advertised output schema mirrors the nested object/array shape."""

    schema = _compile(_read_page()).output_schema
    properties = schema["properties"]

    assert properties["markdown"] == {
        "type": "object",
        "properties": {
            "body": {"type": "string"},
            "body_hash": {"type": "string"},
            "outline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "integer"},
                        "text": {"type": "string"},
                        "slug": {"type": "string"},
                    },
                },
            },
        },
    }
    assert properties["backlinks"] == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "page": {"type": "string"},
                "title": {"type": "string"},
                "display_text": {"type": "string"},
            },
        },
    }


def test_project_shapes_a_nested_row(tiny_schema: None) -> None:
    """Projection recurses: id→sqid, wire→snake child keys, list per-element."""

    compiled = _compile(_read_page())
    row = {
        "id": "PAGE1",
        "title": "Hello",
        "kind": "note",
        "markdown": {
            "body": "# Hi\n\nbody",
            "body_hash": "abc123",
            "outline": [
                {"level": 1, "text": "Hi", "slug": "hi"},
                {"level": 2, "text": "Body", "slug": "body"},
            ],
        },
        "backlinks": [{"page": "PAGE2", "title": "Other", "display_text": "see here"}],
    }

    assert compiled._project(row) == {
        "sqid": "PAGE1",
        "title": "Hello",
        "kind": "note",
        "markdown": {
            "body": "# Hi\n\nbody",
            "body_hash": "abc123",
            "outline": [
                {"level": 1, "text": "Hi", "slug": "hi"},
                {"level": 2, "text": "Body", "slug": "body"},
            ],
        },
        "backlinks": [{"page": "PAGE2", "title": "Other", "display_text": "see here"}],
    }


def test_project_handles_nullable_object_and_empty_list(tiny_schema: None) -> None:
    """A null single object stays null; a missing list projects to an empty list."""

    compiled = _compile(_read_page())

    assert compiled._project({"id": "P", "title": "t", "kind": "k", "markdown": None}) == {
        "sqid": "P",
        "title": "t",
        "kind": "k",
        "markdown": None,
        "backlinks": [],
    }


def test_depth_over_two_fails_fast(tiny_schema: None) -> None:
    """A third object level (markdown → outline → anchor) is rejected at compile time."""

    deep = (
        "sqid",
        ("markdown", (("outline", (("anchor", ("href",)),)),)),
    )
    with pytest.raises(ImproperlyConfigured, match="nests deeper than the 2-level limit"):
        _compile(_read_page(fields=deep))


def test_unknown_nested_child_fails_fast(tiny_schema: None) -> None:
    """An unknown child on a nested object is named at compile time, not at runtime."""

    bad = ("sqid", ("markdown", ("body", "nonexistent")))
    with pytest.raises(ImproperlyConfigured, match="has no field"):
        _compile(_read_page(fields=bad))


def test_module_exposes_project_row_helper() -> None:
    """``project_row`` is the public projection seam downstream stages reuse."""

    assert callable(mcp_graphql.project_row)
