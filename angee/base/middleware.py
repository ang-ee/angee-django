"""Request write attribution after REBAC has resolved the ambient actor."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse

from angee.base.changesets import changeset


class ChangesetMiddleware:
    """Open one changeset for the request, joining any enclosing boundary.

    Django adapts this synchronous middleware for ASGI requests. Streaming
    response iteration happens after the request boundary has closed.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        if not getattr(settings, "ANGEE_CHANGESET_MODEL", None):
            raise MiddlewareNotUsed("No changeset storage is configured.")
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        with changeset() as context:
            response = self.get_response(request)
            if response.status_code >= 400:
                context.outcome = "failure"
            return response
