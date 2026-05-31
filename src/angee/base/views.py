"""HTTP views for serving named GraphQL schemas."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponse
from strawberry.django.views import GraphQLView

from angee.base.graphql.schema import GraphQLSchemas


@lru_cache(maxsize=None)
def _get_view(schema_name: str) -> Any:
    """Return the cached Django view for one named GraphQL schema."""

    schema = GraphQLSchemas.from_discovery().build(schema_name)
    return GraphQLView.as_view(
        schema=schema,
        graphql_ide=getattr(settings, "ANGEE_GRAPHQL_IDE", None),
    )


def graphql_endpoint(request: object, schema_name: str) -> HttpResponse:
    """Dispatch an HTTP request to the named GraphQL schema view."""

    try:
        view = _get_view(schema_name)
    except ImproperlyConfigured as error:
        raise Http404(str(error)) from error
    return view(request)
