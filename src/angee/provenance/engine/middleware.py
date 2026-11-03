"""Native synchronous, asynchronous and WebSocket entrypoint attribution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from asgiref.sync import iscoroutinefunction
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware

from .context import changeset


def _principal(actor: object) -> object | None:
    return actor if getattr(actor, "is_authenticated", True) else None


@sync_and_async_middleware
def ChangesetMiddleware(get_response: Callable[..., Any]) -> Callable[..., Any]:
    """Close HTTP attribution before streaming iteration; hosts own authentication."""
    if iscoroutinefunction(get_response):

        async def async_middleware(request: HttpRequest) -> HttpResponse:
            # Django's async authentication accessor avoids synchronous user lookup.
            actor = await request.auser() if hasattr(request, "auser") else getattr(request, "user", None)
            async with changeset(actor=_principal(actor), fresh=True) as failed:
                response = await cast(Callable[[HttpRequest], Awaitable[HttpResponse]], get_response)(request)
                if response.status_code >= 400:
                    failed()
                return response

        return async_middleware

    def middleware(request: HttpRequest) -> HttpResponse:
        with changeset(actor=_principal(getattr(request, "user", None)), fresh=True) as failed:
            response = cast(HttpResponse, get_response(request))
            if response.status_code >= 400:
                failed()
            return response

    return middleware


class ChangesetScopeMiddleware:
    """Attribute one ASGI WebSocket connection without imposing an ASGI framework."""

    def __init__(self, inner: Callable[..., Awaitable[None]]) -> None:
        self.inner = inner

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "websocket":
            await self.inner(scope, receive, send)
            return
        async with changeset(actor=_principal(scope.get("user")), fresh=True):
            await self.inner(scope, receive, send)
