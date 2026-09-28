"""HTTP views for serving named GraphQL schemas."""

from __future__ import annotations

import json
import logging
from functools import cache
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from strawberry.django.views import GraphQLView

from angee.base.identity import public_id_of
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.view_as import ViewAs
from graphql import GraphQLError, get_operation_ast, parse

logger = logging.getLogger("angee.graphql.view_as")


@cache
def _get_view(schema_name: str) -> Any:
    """Return the Django view for one named GraphQL schema."""

    schema = GraphQLSchemas.from_discovery().build(schema_name)
    return GraphQLView.as_view(
        schema=schema,
        graphql_ide=getattr(settings, "ANGEE_GRAPHQL_IDE", None),
    )


def graphql_endpoint(request: HttpRequest, schema_name: str) -> HttpResponse:
    """Dispatch an HTTP request to the named GraphQL schema view."""

    try:
        view = _get_view(schema_name)
    except ImproperlyConfigured as error:
        raise Http404(str(error)) from error
    try:
        preview = ViewAs.from_request(request)
    except PermissionDenied:
        operation = request.GET.get("operationName")
        document = request.GET.get("query")
        if request.method == "POST":
            try:
                payload = json.loads(request.body)
                if isinstance(payload, dict):
                    operation = payload.get("operationName")
                    document = payload.get("query")
            except (ValueError, UnicodeDecodeError):
                pass
        if operation is None and isinstance(document, str):
            try:
                selected = get_operation_ast(parse(document))
                operation = selected.name.value if selected is not None and selected.name is not None else None
            except GraphQLError:
                pass
        user = getattr(request, "user", None)
        logger.warning(
            "GraphQL view-as denied: real_actor=%s target=%s schema=%s operation=%s",
            public_id_of(user) if user is not None and user.is_authenticated else None,
            request.headers.get("X-Angee-View-As"), schema_name,
            operation if isinstance(operation, str) else None,
        )
        return JsonResponse(
            {"errors": [{"message": "View-as is not permitted.", "extensions": {"code": "FORBIDDEN"}}]}, status=403
        )
    return view(request) if preview is None else preview.dispatch(request, view, schema_name=schema_name)


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
