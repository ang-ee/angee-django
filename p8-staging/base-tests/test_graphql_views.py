"""Tests for GraphQL HTTP view ownership."""

from __future__ import annotations

from typing import Any

from django.http import HttpResponse
from strawberry.django.views import GraphQLView as SyncGraphQLView

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
