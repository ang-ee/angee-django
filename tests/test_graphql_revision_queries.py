"""Protected GraphQL queries and writes through native revision middleware."""

from __future__ import annotations

import json
from typing import Any

import reversion
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships
from rebac.models import SchemaDefinition
from reversion.middleware import RevisionMiddleware
from reversion.models import Version

from angee.knowledge import schema as knowledge_schema
from tests.conftest import MarkdownPage, Page, addon_schema, create_user, execute_schema, vault_for


def _post(schema: Any, user: Any, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Exercise the same native POST revision boundary used by IAM autoconfig."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user

    def view(request: HttpRequest) -> JsonResponse:
        assert connection.in_atomic_block
        assert reversion.is_active()
        result = execute_schema(schema, query, variables, request=request)
        payload = {"data": result.data}
        if result.errors:
            payload["errors"] = [error.formatted for error in result.errors]
        return JsonResponse(payload)

    response = RevisionMiddleware(view)(request)
    return json.loads(response.content)


def test_protected_graphql_post_loads_one_schema_for_one_or_many_rows(knowledge_tables: None) -> None:
    """A read-only POST reuses its coherent permission schema inside the revision."""

    owner = create_user("query-owner")
    outsider = create_user("query-outsider")
    vault = vault_for(owner)
    private_vault = vault_for(outsider, name="Private")
    with actor_context(owner):
        for index in range(25):
            Page.objects.create_in(vault, title=f"Page {index:02}")
    with actor_context(outsider):
        Page.objects.create_in(private_vault, title="Hidden")

    schema = addon_schema(knowledge_schema.schemas, "public")
    query = """
        query PageRows($limit: Int!) {
          pages(limit: $limit, order_by: [{title: asc}]) { id title kind updated_at }
          pages_aggregate { aggregate { count } }
        }
    """
    schema_table = connection.ops.quote_name(SchemaDefinition._meta.db_table)
    schema_reads = []
    query_counts = []
    versions_before = Version.objects.count()
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            payload = _post(schema, owner, query, {"limit": size})
        assert "errors" not in payload, payload
        assert len(payload["data"]["pages"]) == size
        assert all(row["title"] != "Hidden" for row in payload["data"]["pages"])
        assert payload["data"]["pages_aggregate"]["aggregate"]["count"] == 25
        schema_reads.append(sum(f"FROM {schema_table}" in item["sql"] for item in captured))
        query_counts.append(len(captured))

    assert Version.objects.count() == versions_before
    assert schema_reads == [1, 1], "Each read-only HTTP transaction should load its permission schema once"
    assert query_counts[0] == query_counts[1], "Scalar row reads must not grow with page size"


def test_graphql_post_preserves_revisions_and_rejects_reader_writes(knowledge_tables: None) -> None:
    """The native request revision records accepted edits and excludes denied writes."""

    owner = create_user("revision-owner")
    reader = create_user("revision-reader")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Versioned")
        markdown = MarkdownPage.objects.write_body(page, "Original body")
    write_relationships(
        [
            RelationshipTuple(resource=to_object_ref(vault), relation="viewer", subject=to_subject_ref(reader)),
        ]
    )
    schema = addon_schema(knowledge_schema.schemas, "public")
    query = """
        mutation WriteBody($page: ID!, $body: String!) {
          update_page_body(page: $page, body: $body) { ok markdown { body } }
        }
    """
    before = Version.objects.get_for_object(markdown).count()
    accepted = _post(schema, owner, query, {"page": str(page.sqid), "body": "Accepted body"})
    assert "errors" not in accepted, accepted
    assert accepted["data"]["update_page_body"] == {"ok": True, "markdown": {"body": "Accepted body"}}
    versions = Version.objects.get_for_object(markdown)
    assert versions.count() == before + 1
    latest = versions.first()
    assert latest is not None
    assert latest.field_dict["body"] == "Accepted body"
    assert latest.revision.user_id == owner.pk

    denied = _post(schema, reader, query, {"page": str(page.sqid), "body": "Forbidden body"})
    assert denied["errors"][0]["extensions"]["code"] == "PERMISSION_DENIED"
    with actor_context(reader):
        markdown.refresh_from_db()
        assert markdown.body == "Accepted body"
    assert Version.objects.get_for_object(markdown).count() == before + 1
