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

from angee.base.hooks import resolve_hook
from angee.graphql.schema import GraphQLSchemas


class AngeeGraphQLView(GraphQLView):
    """Native view carrying its declared named-schema identity."""

    schema_name = ""


@cache
def _get_view(schema_name: str) -> Any:
    """Return the Django view for one named GraphQL schema."""

    schema = GraphQLSchemas.from_discovery().build(schema_name)
    view_class = resolve_hook("ANGEE_GRAPHQL_HTTP_VIEW_CLASS") or AngeeGraphQLView
    if not isinstance(view_class, type) or not issubclass(view_class, AngeeGraphQLView):
        raise ImproperlyConfigured("GraphQL view must extend AngeeGraphQLView.")
    return view_class.as_view(
        schema_name=schema_name,
        schema=schema,
        graphql_ide=getattr(settings, "ANGEE_GRAPHQL_IDE", None),
    )


def graphql_endpoint(request: HttpRequest, schema_name: str) -> HttpResponse:
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
    learns the cookie name here and sends its current value as ``X-CSRFToken``.
    Login rotates the cookie, so clients must not retain a pre-login token.
    """

    return JsonResponse(
        {
            "token": get_token(request),
            "cookieName": (
                None if settings.CSRF_USE_SESSIONS or settings.CSRF_COOKIE_HTTPONLY else settings.CSRF_COOKIE_NAME
            ),
        }
    )
