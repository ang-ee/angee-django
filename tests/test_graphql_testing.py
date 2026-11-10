"""The shipped GraphQL document guard: actor binding, captured SQL and fixed ceilings."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.graphql.testing.documents import GraphQLDocumentRead, measure_graphql_document
from angee.projects import schema as projects_schema
from angee.projects.testing.models import Project, Task
from tests.conftest import addon_schema, create_user

TASKS = "query Tasks($limit: Int!) { project_tasks(limit: $limit) { id title } }"


def test_read_binds_the_actor_and_records_the_sql_django_captured(composed_tables: None) -> None:
    del composed_tables
    owner, outsider = create_user("document-guard-owner"), create_user("document-guard-outsider")
    with actor_context(owner):
        project = Project.objects.create(title="Guarded")
        tasks = [Task.objects.create(project=project, title=f"Guarded {index}") for index in range(2)]
    schema = addon_schema(projects_schema.schemas, "console")

    with CaptureQueriesContext(connection) as captured:
        read = measure_graphql_document(schema, TASKS, {"limit": 5}, actor=owner)

    assert sorted(row["id"] for row in read.data["project_tasks"]) == sorted(task.sqid for task in tasks)
    assert read.statements == len(captured) > 0
    assert read.sql_bytes == sum(len(query["sql"].encode("utf-8")) for query in captured)
    assert measure_graphql_document(schema, TASKS, {"limit": 5}, actor=outsider).data == {"project_tasks": []}
    with pytest.raises(AssertionError):
        measure_graphql_document(schema, "{ project_tasks { missing_field } }", {}, actor=owner)


def test_ceiling_holds_at_the_measured_cost_and_fails_above_it() -> None:
    read = GraphQLDocumentRead(data={}, statements=3, sql_bytes=2049)

    read.assert_ceiling(statements=3)
    with pytest.raises(AssertionError, match="issued 3 SQL statements; ceiling 2"):
        read.assert_ceiling(statements=2)
