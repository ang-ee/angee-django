"""Read an addon's authored GraphQL documents, execute documents as one actor, pin the SQL they issue and
witness that they show no row their actor cannot read."""

from __future__ import annotations

import re
from collections.abc import Iterator
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
from strawberry_django.utils.typing import get_django_definition

from graphql import (
    DocumentNode,
    FieldNode,
    NameNode,
    OperationDefinitionNode,
    SelectionSetNode,
    Source,
    Visitor,
    parse,
    print_ast,
    visit,
)

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


@dataclass(frozen=True)
class UnreadableRow:
    """One row a document returned that its actor's REBAC ``read`` does not admit."""

    path: str
    model: str
    public_id: str


def unreadable_rows(schema: Any, document: str, variables: dict[str, Any], *, actor: Any) -> list[UnreadableRow]:
    """Execute ``document`` as ``actor``; return every row it named that the actor's REBAC ``read`` denies.

    The invariant every surface keeps, whatever policy REBAC evaluates
    (``REBAC_INHERIT_THROUGH_LINKS``): a document shows no row its actor cannot
    read. The document must succeed; :func:`unreadable_in` reads its data.
    """

    typed = with_typenames(document)
    return unreadable_in(schema, result_data(execute_schema(schema, typed, variables, user=actor)), actor=actor)


def with_typenames(document: str) -> str:
    """``document`` selecting ``__typename`` beside every object selection, as :func:`unreadable_in` reads it."""

    return print_ast(visit(parse(document), _WithTypenames()))


def unreadable_in(schema: Any, data: Any, *, actor: Any) -> list[UnreadableRow]:
    """Every row ``data`` names that ``actor``'s REBAC ``read`` denies.

    The library decides each row the data names, in that model's ``read``
    queryset for ``actor`` (:func:`~angee.base.permissions.reader_queryset`), not
    the path that delivered it. A row is named by:

    - an object with an ``id`` whose GraphQL type projects a REBAC model (select
      ``__typename``: :func:`with_typenames`);
    - the rows such a model says a readable row speaks for (``speaks_for``: a
      notification's message, whose existence, time and count the row reveals),
      read from the stored row;
    - any public id anywhere in a returned string, by its model's unique sqid
      prefix (``angee.graphql.ids.assert_unique_sqid_prefixes``): an id scalar of
      any name, a key or a reference string that embeds one. No field declares
      it, so a field, a type or a document added later is read the same way.

    It cannot see what a projection discloses without naming a row: a name, a
    count, a preview or a flag computed from rows the reader may not read, a
    mutation's or a subscription's effects. Those need their own assertion. An
    empty list is the invariant held.
    """

    # The model owners need the app registry; this module imports before it.
    from rebac import RebacMixin

    from angee.base.models.identity import public_id_for
    from angee.base.permissions import reader_queryset
    from angee.graphql.ids import instance_for_id

    readable: dict[tuple[type[Any], str], bool] = {}
    denied: list[UnreadableRow] = []

    def reads(model: Any, public_id: str) -> bool:
        return issubclass(model, RebacMixin) and instance_for_id(
            model, public_id, queryset=reader_queryset(model, actor),
        ) is not None

    def check(path: str, model: Any, public_id: str, *, as_any: tuple[Any, ...] = ()) -> None:
        if model is None or not issubclass(model, RebacMixin):
            return
        key = (model, public_id)
        if key not in readable:
            # A multi-table row is one record: its id is read where the reader reads it as any of its types.
            readable[key] = any(reads(candidate, public_id) for candidate in (model, *as_any))
        if not readable[key]:
            denied.append(UnreadableRow(path, model._meta.label, public_id))

    for path, row in _typed_objects(data):
        definition = schema.get_type_by_name(row["__typename"])
        origin = getattr(definition, "origin", None)
        django = get_django_definition(origin) if origin is not None else None
        model = django.model if django is not None else None
        if model is not None and row.get("id"):
            public_id = str(row["id"])
            check(path, model, public_id)
            spoken = getattr(model, "speaks_for", ())
            stored = instance_for_id(model, public_id, queryset=model._base_manager.all()) if spoken else None
            for name in spoken if stored is not None else ():
                spoken_field = cast(Any, stored)._meta.get_field(name)
                target_id = spoken_field.value_from_object(stored)
                if target_id is not None:
                    target = spoken_field.related_model
                    check(f"{path}<{name}>", target, public_id_for(target, target_id))
    models_by_prefix, pattern = _public_id_prefixes()
    for path, text in _strings(data):
        for match in pattern.finditer(text) if pattern is not None else ():
            model = models_by_prefix[match.group(1)]
            if instance_for_id(model, match.group(0), queryset=model._base_manager.all()) is not None:
                children = tuple(
                    child for child in _multi_table_children(model)
                    if instance_for_id(child, match.group(0), queryset=child._base_manager.all()) is not None
                )
                check(path, model, match.group(0), as_any=children)
    return denied


def _multi_table_children(model: type[Any]) -> tuple[type[Any], ...]:
    """The installed models that extend ``model`` through multi-table inheritance, at any depth."""

    from django.apps import apps

    return tuple(
        candidate for candidate in apps.get_models()
        if candidate is not model and model in candidate._meta.get_parent_list()
    )


def _public_id_prefixes() -> tuple[dict[str, type[Any]], re.Pattern[str] | None]:
    """Every installed model's sqid prefix, and one pattern that finds a public id of any of them."""

    from django.apps import apps

    from angee.base.identity import public_data_id_field

    found: dict[str, type[Any]] = {}
    for model in apps.get_models():
        field = public_data_id_field(model)
        if field is not None and field.prefix and field.model is model:
            found.setdefault(field.prefix, model)
    if not found:
        return found, None
    alternatives = "|".join(re.escape(prefix) for prefix in sorted(found, key=len, reverse=True))
    return found, re.compile(rf"(?<![A-Za-z0-9_])({alternatives})[A-Za-z0-9]+")


def _strings(data: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Yield ``(path, text)`` for every string the data returns."""

    if isinstance(data, str):
        yield path, data
    elif isinstance(data, dict):
        for name, value in data.items():
            yield from _strings(value, f"{path}.{name}")
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from _strings(value, f"{path}[{index}]")


class _WithTypenames(Visitor):
    """Select ``__typename`` beside every object selection, so each returned object names its type."""

    def enter_selection_set(self, node: SelectionSetNode, key: Any, parent: Any, *_args: Any) -> Any:
        if isinstance(parent, OperationDefinitionNode) or any(
            isinstance(selection, FieldNode) and selection.alias is None and selection.name.value == "__typename"
            for selection in node.selections
        ):
            return None
        return SelectionSetNode(selections=(*node.selections, FieldNode(name=NameNode(value="__typename"))))


def _typed_objects(data: Any, path: str = "$") -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(path, object)`` for every returned object that names its type."""

    if isinstance(data, dict):
        if isinstance(data.get("__typename"), str):
            yield path, data
        for name, value in data.items():
            yield from _typed_objects(value, f"{path}.{name}")
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from _typed_objects(value, f"{path}[{index}]")
