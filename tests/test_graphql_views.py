"""Tests for GraphQL HTTP view ownership."""

from __future__ import annotations

import json
from typing import Any

import pytest
import strawberry
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from strawberry.django.views import GraphQLView as SyncGraphQLView
from strawberry.scalars import JSON

from angee.base.serialization import MAX_JSON_DOCUMENT_DEPTH
from angee.graphql import views


def test_graphql_endpoint_uses_sync_django_graphql_view() -> None:
    """Blocking resolver I/O stays safe because HTTP GraphQL uses the sync view."""

    assert views.GraphQLView is SyncGraphQLView


def test_get_view_reuses_built_schema_per_name(monkeypatch) -> None:
    """The HTTP endpoint builds each named GraphQL view once per process."""

    build_calls: list[str] = []

    class Discovery:
        """Stub GraphQL schema discovery owner."""

        def build(self, name: str) -> object:
            build_calls.append(name)
            return object()

    def as_view(**kwargs: Any) -> Any:
        def view(request: object) -> HttpResponse:
            del request
            return HttpResponse("ok")

        view.kwargs = kwargs  # type: ignore[attr-defined]
        return view

    monkeypatch.setattr(views.GraphQLSchemas, "from_discovery", classmethod(lambda cls: Discovery()))
    monkeypatch.setattr(views.GraphQLView, "as_view", as_view)
    cache_clear = getattr(views._get_view, "cache_clear", lambda: None)
    cache_clear()
    try:
        first = views._get_view("public")
        second = views._get_view("public")
    finally:
        cache_clear()

    assert first is second
    assert build_calls == ["public"]


@strawberry.type
class EchoQuery:
    @strawberry.field
    def echo(self, value: JSON) -> JSON:
        return value


def echo_body(depth: int) -> str:
    """An echo request whose innermost member sits in ``depth`` containers, two of them the body and its variables."""

    levels = depth - 2
    return (
        '{"query": "query Echo($value: JSON!) { echo(value: $value) }", "variables": {"value": '
        + "[" * levels + "0" + "]" * levels + "}}"
    )


def serve_echo(monkeypatch: pytest.MonkeyPatch, body: str, headers: dict[str, str]) -> HttpResponse:
    class Discovery:
        def build(self, name: str) -> object:
            return strawberry.Schema(query=EchoQuery)

    request = RequestFactory().post("/graphql/public/", data=body, content_type="application/json", **headers)
    request.user = AnonymousUser()
    monkeypatch.setattr(views.GraphQLSchemas, "from_discovery", classmethod(lambda cls: Discovery()))
    cache_clear = getattr(views._get_view, "cache_clear", lambda: None)
    cache_clear()
    try:
        return views.graphql_endpoint(request, "public")
    finally:
        cache_clear()


@pytest.mark.parametrize(("headers", "status"), [
    pytest.param({}, 400, id="a-request"),
    pytest.param({"HTTP_X_ANGEE_VIEW_AS": "usr_someone"}, 403, id="a-refused-view-as-preview"),
])
def test_a_body_nested_past_the_document_bound_is_a_client_error(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str], status: int,
) -> None:
    """A level past ``MAX_JSON_DOCUMENT_DEPTH`` is malformed JSON: Strawberry's 400, or the view-as refusal past it."""

    response = serve_echo(monkeypatch, echo_body(MAX_JSON_DOCUMENT_DEPTH + 1), headers)

    assert response.status_code == status
    if status == 400:
        assert response.content == b"Unable to parse request body as JSON"
    else:
        assert json.loads(response.content)["errors"][0]["extensions"] == {"code": "FORBIDDEN"}


def test_a_body_at_the_document_bound_is_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = echo_body(MAX_JSON_DOCUMENT_DEPTH)

    response = serve_echo(monkeypatch, body, {})

    assert response.status_code == 200
    assert json.loads(response.content) == {"data": {"echo": json.loads(body)["variables"]["value"]}}
