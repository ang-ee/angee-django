"""Actor-bound GraphQL document and SQL budgets for IAM test hosts."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

from django.db import connections
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.iam.testing.queries import is_permission_audit_insert


def graphql_request(user: Any) -> Any:
    """Return a bare POST request carrying ``user``."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    return request


def execute_schema(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a request-shaped actor context."""

    # The shared fixture plugin imports before django.setup(); the auth model
    # import stays inside the call that needs it.
    from django.contrib.auth.models import AnonymousUser

    request = request or graphql_request(user or AnonymousUser())
    actor = getattr(request, "user", AnonymousUser())
    with actor_context(actor):
        return schema.execute_sync(
            query,
            variable_values=variables or {},
            context_value=SimpleNamespace(request=request),
        )


def result_data(result: Any) -> dict[str, Any]:
    """Return result data after asserting the operation succeeded."""

    assert result.errors is None, result.errors
    assert result.data is not None
    return cast(dict[str, Any], result.data)


@dataclass(frozen=True)
class GraphQLDocumentRead:
    """SQL issued by one actor-bound GraphQL document execution."""

    data: dict[str, Any] = field(repr=False)
    statement_count: int
    total_sql_bytes: int
    largest_sql_bytes: int
    audit_inserts: int
    statements: tuple[str, ...] = field(default=(), repr=False)

    def assert_bytes(self, *, total: int, largest: int, cause: str = "") -> None:
        """Pin both the complete document and its largest generated statement."""

        explanation = f" ({cause})" if cause else ""
        assert self.total_sql_bytes <= total, (
            f"GraphQL document SQL: {self.total_sql_bytes} bytes exceeds total budget {total} bytes{explanation}"
        )
        assert self.largest_sql_bytes <= largest, (
            f"GraphQL largest statement: {self.largest_sql_bytes} bytes exceeds budget {largest} bytes{explanation}"
        )

    def assert_statements(self, maximum: int) -> None:
        """Pin fixed per-document statement overhead."""

        assert self.statement_count <= maximum, (
            f"GraphQL document statements: {self.statement_count} exceeds budget {maximum}"
        )

    def assert_audit_inserts(self, maximum: int) -> None:
        """Pin permission-audit writes while a fixing lane remains open."""

        assert self.audit_inserts <= maximum, (
            f"GraphQL document permission-audit INSERTs: {self.audit_inserts} exceeds budget {maximum}"
        )

    def assert_no_audit_writes(self) -> None:
        """A read must not issue permission-audit INSERT statements."""

        self.assert_audit_inserts(0)

    def assert_flat_from(self, smaller: GraphQLDocumentRead) -> None:
        """A list's statement count must be identical at two fixture sizes."""

        assert self.statement_count == smaller.statement_count, (
            f"GraphQL list statements: smaller={smaller.statement_count}, larger={self.statement_count}"
        )


def measure_graphql_document(
    schema: Any,
    document: str,
    variables: dict[str, Any],
    *,
    actor: Any,
) -> GraphQLDocumentRead:
    """Capture an authored document using Django's native query recorder.

    SQL bytes count UTF-8 bytes of Django's captured SQL text, including bound
    values where the backend renders them. Each INSERT into REBAC's audit table
    is one audit write.
    """

    with ExitStack() as stack:
        captures = [(database, stack.enter_context(CaptureQueriesContext(database))) for database in connections.all()]
        data = result_data(execute_schema(schema, document, variables, user=actor))
    sql = [(database, query["sql"]) for database, capture in captures for query in capture]
    sizes = [len(statement.encode("utf-8")) for _, statement in sql]
    return GraphQLDocumentRead(
        data=data,
        statement_count=len(sql),
        total_sql_bytes=sum(sizes),
        largest_sql_bytes=max(sizes, default=0),
        audit_inserts=sum(is_permission_audit_insert(statement, database) for database, statement in sql),
        statements=tuple(statement for _, statement in sql),
    )
