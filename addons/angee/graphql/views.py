"""HTTP views for serving named GraphQL schemas."""

from __future__ import annotations

from functools import cache
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from strawberry.django.views import GraphQLView

from angee.graphql.schema import GraphQLSchemas


@cache
def _get_view(schema_name: str) -> Any:
    """Return the Django view for one named GraphQL schema."""

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


@ensure_csrf_cookie
def csrf_token(request: HttpRequest) -> JsonResponse:
    """Set the CSRF cookie and return its token for the SPA to echo.

    The session-cookie GraphQL endpoints are CSRF-protected; a browser client
    fetches this once and sends the token as ``X-CSRFToken`` on every mutation.
    """

    return JsonResponse({"token": get_token(request)})
