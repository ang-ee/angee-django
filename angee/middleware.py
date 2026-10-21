"""Request and connection attribution after REBAC actor resolution.

Streaming response iteration runs after the request boundary closes; streaming
writers must open their own changeset around iteration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from asgiref.sync import iscoroutinefunction
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware

from angee.asgi import ASGIApp, Receive, Scope, Send
from angee.changesets import changeset, changeset_model_label

FAILED_STATUS = 400
"""The first response status that closes a request's changeset as failed."""


@sync_and_async_middleware
def ChangesetMiddleware(
    get_response: Callable[[HttpRequest], HttpResponse | Awaitable[HttpResponse]],
) -> Callable[[HttpRequest], HttpResponse | Awaitable[HttpResponse]]:
    """Own a fresh boundary for each request on the stack's own sync or async path.

    A GraphQL response reports handled errors with status 200; the GraphQL view
    marks those on the boundary itself.
    """

    if not changeset_model_label():
        raise MiddlewareNotUsed("No changeset storage is configured.")
    if iscoroutinefunction(get_response):
        async_response = cast(Callable[[HttpRequest], Awaitable[HttpResponse]], get_response)

        async def async_middleware(request: HttpRequest) -> HttpResponse:
            async with changeset(fresh=True) as mark_failed:
                response = await async_response(request)
                if response.status_code >= FAILED_STATUS:
                    mark_failed()
                return response

        return async_middleware
    sync_response = cast(Callable[[HttpRequest], HttpResponse], get_response)

    def middleware(request: HttpRequest) -> HttpResponse:
        with changeset(fresh=True) as mark_failed:
            response = sync_response(request)
            if response.status_code >= FAILED_STATUS:
                mark_failed()
            return response

    return middleware


class ChangesetScopeMiddleware:
    """Own a fresh boundary for the life of one ASGI connection scope.

    Channels consumers (GraphQL subscriptions, the agent ACP transport) run every
    handler and ``database_sync_to_async`` call inside the connection's context,
    so their captured writes attribute to one changeset per connection, closed as
    failed when the consumer raises. Composed by :func:`angee.asgi.websocket_application`.
    """

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with changeset(fresh=True):
            await self.inner(scope, receive, send)
