"""Read an addon's authored GraphQL documents, execute documents as one actor and pin the SQL they issue."""

from __future__ import annotations

import re
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connections
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from graphql import DocumentNode, Source, parse

_AUTHORED_DOCUMENT = re.compile(r"graphql\(`(.*?)`\)", re.S)


def authored_documents(*paths: Path) -> list[DocumentNode]:
    """Parse every ``graphql(`...`)`` document the web ``documents*.ts`` files at ``paths`` author.

    Each document keeps its file as the source name, so a validation error names
    the file. ``graphql.concat_ast`` joins them into the one document host codegen
    validates.
    """

    return [
        parse(Source(body, str(path)))
        for path in paths
        for body in _AUTHORED_DOCUMENT.findall(path.read_text(encoding="utf-8"))
    ]


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
    ``repeated`` counts the statements beyond the first of each statement text,
    bound values apart: a lookup issued once per row and a read asked twice both
    repeat a text, which a statement ceiling alone cannot tell from a new read.
    """

    data: dict[str, Any] = field(repr=False)
    statements: int
    sql_bytes: int
    repeated: int = 0

    def assert_ceiling(self, *, statements: int, repeated: int | None = None) -> None:
        """Fail when the read exceeds its fixed statement ceiling, or its repeated-text ceiling when given.

        A ceiling is today's measured cost: a change that lowers the cost lowers
        it, and it is never raised to make a test pass.
        """

        assert self.statements <= statements, (
            f"GraphQL document issued {self.statements} SQL statements; ceiling {statements}. "
            "Ratchet downward when cost falls; never raise to admit a regression."
        )
        assert repeated is None or self.repeated <= repeated, (
            f"GraphQL document repeated a statement text {self.repeated} times; ceiling {repeated}. "
            "A repeat is a per-row lookup or a read asked twice: batch or share it, never raise the ceiling."
        )


def measure_graphql_document(
    schema: Any,
    document: str,
    variables: dict[str, Any],
    *,
    actor: Any,
) -> GraphQLDocumentRead:
    """Execute ``document`` as ``actor`` and record the SQL Django captured."""

    texts: list[str] = []

    def record(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        texts.append(sql)
        return execute(sql, params, many, context)

    with ExitStack() as stack:
        captures = [stack.enter_context(CaptureQueriesContext(connection)) for connection in connections.all()]
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(record))
        data = result_data(execute_schema(schema, document, variables, user=actor))
    sizes = [len(query["sql"].encode("utf-8")) for capture in captures for query in capture]
    return GraphQLDocumentRead(
        data=data, statements=len(sizes), sql_bytes=sum(sizes), repeated=len(texts) - len(set(texts))
    )
