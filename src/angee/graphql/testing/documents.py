"""Execute GraphQL documents as one actor and pin the SQL they issue."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connections
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import actor_context


def graphql_request(user: Any, *, session: bool = False) -> Any:
    """Return a bare GraphQL POST request carrying ``user``; no resolver reads its path.

    ``session`` binds the configured native Django session backend, as login and
    logout verbs need.
    """

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    if session:
        SessionMiddleware(lambda _request: HttpResponse()).process_request(request)
    return request


def execute_schema(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
    session: bool = False,
) -> Any:
    """Execute a GraphQL operation as the request's user.

    The actor context wraps execution as ``rebac.middleware.ActorMiddleware``
    does for a served request; without a ``request``, ``graphql_request`` builds
    one for ``user``, with a session when ``session`` asks for it.
    """

    # Django's auth models need the app registry; this module imports before it.
    from django.contrib.auth.models import AnonymousUser

    request = request or graphql_request(user or AnonymousUser(), session=session)
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
    """The data and the SQL of one actor-bound GraphQL document execution.

    ``statements`` counts the statements Django captured on every configured
    database; ``sql_bytes`` sums their UTF-8 SQL text, including bound values
    where the backend renders them, for comparing reads within one run.
    """

    data: dict[str, Any] = field(repr=False)
    statements: int
    sql_bytes: int

    def assert_ceiling(self, *, statements: int) -> None:
        """Fail when the read exceeds its fixed statement ceiling.

        A ceiling is today's measured cost: a change that lowers the cost lowers
        it, and it is never raised to make a test pass.
        """

        assert self.statements <= statements, (
            f"GraphQL document issued {self.statements} SQL statements; ceiling {statements}. "
            "Ratchet downward when cost falls; never raise to admit a regression."
        )


def measure_graphql_document(
    schema: Any,
    document: str,
    variables: dict[str, Any],
    *,
    actor: Any,
) -> GraphQLDocumentRead:
    """Execute ``document`` as ``actor`` and record the SQL Django captured."""

    with ExitStack() as stack:
        captures = [stack.enter_context(CaptureQueriesContext(connection)) for connection in connections.all()]
        data = result_data(execute_schema(schema, document, variables, user=actor))
    sizes = [len(query["sql"].encode("utf-8")) for capture in captures for query in capture]
    return GraphQLDocumentRead(data=data, statements=len(sizes), sql_bytes=sum(sizes))
