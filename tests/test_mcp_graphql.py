"""MCP GraphQL compilation, nested projection and actor-scoped read contracts.

Builds a tiny Strawberry schema shaped like the knowledge ``read_page`` projection
(a nullable nested object, a nested list, a list of objects) and drives the compiler
in :mod:`angee.mcp.graphql` directly, so the assertions cover the document rendering,
output schema, and row projection without standing up the full discovery schema.
Native scoped documents also exercise the request-less MCP execution boundary.
"""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
import strawberry_django
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed

from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.schema import GraphQLSchemas
from angee.mcp import graphql as mcp_graphql
from angee.mcp.graphql import GraphQLTool, _compile, execute_under_actor
from angee.testing.permissions import install_permission_schema
from tests.conftest import SchemaAddon, create_user
from tests.scopedemo.models import Scope, ScopedDoc


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


@pytest.mark.usefixtures("tiny_schema")
def test_flat_spec_still_compiles() -> None:
    """A scalar-only spec keeps the flat behavior: a flat selection and flat projection."""

    compiled = _compile(_read_page(fields=("sqid", "title", "kind")))

    assert compiled.document == "query ($id: ID!) { page(id: $id) { id title kind } }"
    assert compiled._project({"id": "P1", "title": "Hi", "kind": "note"}) == {
        "sqid": "P1",
        "title": "Hi",
        "kind": "note",
    }
    assert compiled.output_schema is not None
    assert compiled.output_schema["properties"]["sqid"] == {"type": "string"}


@pytest.mark.usefixtures("tiny_schema")
def test_nested_spec_compiles_to_a_nested_document() -> None:
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


@pytest.mark.usefixtures("tiny_schema")
def test_nested_output_schema_describes_objects_and_arrays() -> None:
    """The advertised output schema mirrors the nested object/array shape."""

    schema = _compile(_read_page()).output_schema
    assert schema is not None
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


@pytest.mark.usefixtures("tiny_schema")
def test_project_shapes_a_nested_row() -> None:
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


@pytest.mark.usefixtures("tiny_schema")
def test_project_handles_nullable_object_and_empty_list() -> None:
    """A null single object stays null; a missing list projects to an empty list."""

    compiled = _compile(_read_page())

    assert compiled._project({"id": "P", "title": "t", "kind": "k", "markdown": None}) == {
        "sqid": "P",
        "title": "t",
        "kind": "k",
        "markdown": None,
        "backlinks": [],
    }


@pytest.mark.usefixtures("tiny_schema")
def test_depth_over_two_fails_fast() -> None:
    """A third object level (markdown → outline → anchor) is rejected at compile time."""

    deep = (
        "sqid",
        ("markdown", (("outline", (("anchor", ("href",)),)),)),
    )
    with pytest.raises(ImproperlyConfigured, match="nests deeper than the 2-level limit"):
        _compile(_read_page(fields=deep))


@pytest.mark.usefixtures("tiny_schema")
def test_unknown_nested_child_fails_fast() -> None:
    """An unknown child on a nested object is named at compile time, not at runtime."""

    bad = ("sqid", ("markdown", ("body", "nonexistent")))
    with pytest.raises(ImproperlyConfigured, match="has no field"):
        _compile(_read_page(fields=bad))


def test_module_exposes_project_row_helper() -> None:
    """``project_row`` is the public projection seam downstream stages reuse."""

    assert callable(mcp_graphql.project_row)


@strawberry_django.type(Scope)
class McpScopeType(AngeeNode):
    name: strawberry.auto


@strawberry_django.type(ScopedDoc)
class McpDocType(AngeeNode):
    title: str | None
    scope: McpScopeType | None = actor_scoped_to_one("scope")


def test_mcp_reads_conceal_gated_fields_rows_totals_and_group_keys(
    transactional_db: None, monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """G3/G5 hold through the actual request-less MCP execution and actor binding."""

    settings.REBAC_SUPERUSER_BYPASS = False
    settings.REBAC_STRICT_MODE = True
    # Bare test settings omit this base autoconfig default; match composed hosts.
    settings.REBAC_FIELD_READ_MODE = "redact"
    viewer = create_user("mcp-reader")
    owner = create_user("mcp-owner")
    assert not viewer.is_superuser and not viewer.is_staff
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    install_permission_schema(parse_zed("""
        definition auth/user {}
        definition scopedemo/scope {
            relation reader: auth/user
            permission read = reader
        }
        definition scopedemo/doc {
            relation reader: auth/user
            relation secret_reader: auth/user
            permission read = reader + secret_reader
            permission read__title = secret_reader
        }
    """))
    try:
        resource = hasura_model_resource(
            McpDocType, model=ScopedDoc, name="mcp_docs",
            filterable=["id"], sortable=["id"], aggregatable=["id"], groupable=["scope"],
            insert=False, update=False, delete=False,
        )
        schemas = GraphQLSchemas([
            SchemaAddon({"public": {"query": (resource.query,), "types": tuple(resource.types)}}),
        ])
        monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
        with system_context(reason="MCP hidden-read fixture"):
            visible_scope = Scope.objects.create(name="Visible scope")
            hidden_scope = Scope.objects.create(name="Hidden parent")
            hidden_row_scope = Scope.objects.create(name="Only the hidden row uses this group")
            visible = ScopedDoc.objects.create(scope=visible_scope, title="Gated visible title")
            redacted_parent = ScopedDoc.objects.create(scope=hidden_scope, title="Gated nested title")
            hidden = ScopedDoc.objects.create(scope=hidden_row_scope, title="Hidden row title")
            write_relationships([
                *(RelationshipTuple(to_object_ref(row), "reader", to_subject_ref(viewer))
                  for row in (visible_scope, hidden_row_scope, visible, redacted_parent)),
                *(RelationshipTuple(to_object_ref(row), "reader", to_subject_ref(owner))
                  for row in (visible_scope, hidden_scope, hidden_row_scope)),
                *(RelationshipTuple(to_object_ref(row), "secret_reader", to_subject_ref(owner))
                  for row in (visible, redacted_parent, hidden)),
            ])
        document = """
            query HiddenReads($id: String!) {
              detail: mcp_docs_by_pk(id: $id) { id title }
              rows: mcp_docs { id title scope { id name } }
              totals: mcp_docs_aggregate { aggregate { count } }
              groups: mcp_docs_groups(group_by: [{field: SCOPE}]) {
                key { scope_id }
                aggregate { count }
              }
            }
        """
        with actor_context(viewer):
            data = async_to_sync(execute_under_actor)("public", document, {"id": hidden.sqid})
        assert data["detail"] is None
        assert {row["id"]: row for row in data["rows"]} == {
            visible.sqid: {
                "id": visible.sqid, "title": None,
                "scope": {"id": visible_scope.sqid, "name": "Visible scope"},
            },
            redacted_parent.sqid: {"id": redacted_parent.sqid, "title": None, "scope": None},
        }
        assert data["totals"] == {"aggregate": {"count": 2}}
        assert {row["key"]["scope_id"]: row["aggregate"]["count"] for row in data["groups"]} == {
            visible_scope.sqid: 1, None: 1,
        }
        # Positive control: the same entry point exposes the data when its actor holds the grants.
        with actor_context(owner):
            readable = async_to_sync(execute_under_actor)("public", document, {"id": hidden.sqid})
        assert readable["detail"] == {"id": hidden.sqid, "title": "Hidden row title"}
        assert all(row["title"] is not None and row["scope"] is not None for row in readable["rows"])
        assert readable["totals"] == {"aggregate": {"count": 3}}
        assert {row["key"]["scope_id"] for row in readable["groups"]} == {
            visible_scope.sqid, hidden_scope.sqid, hidden_row_scope.sqid,
        }
    finally:
        reset_backend()
