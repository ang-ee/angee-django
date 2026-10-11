"""Request attribution after REBAC actor resolution.

Streaming response iteration runs after this boundary closes; streaming writers
must open their own changeset around iteration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async
from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware

from angee.base.autoconfig import SETTINGS
from angee.base.changesets import changeset


@sync_and_async_middleware
def ChangesetMiddleware(
    get_response: Callable[[HttpRequest], HttpResponse | Awaitable[HttpResponse]],
) -> Callable[[HttpRequest], HttpResponse | Awaitable[HttpResponse]]:
    """Own a fresh boundary for each synchronous or asynchronous request."""

    if not getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]):
        raise MiddlewareNotUsed("No changeset storage is configured.")
    asynchronous = iscoroutinefunction(get_response)
    sync_response = (
        async_to_sync(cast(Callable[[HttpRequest], Awaitable[HttpResponse]], get_response))
        if asynchronous
        else cast(Callable[[HttpRequest], HttpResponse], get_response)
    )

    def middleware(request: HttpRequest) -> HttpResponse:
        with changeset(fresh=True) as mark_failed:
            response = sync_response(request)
            if response.status_code >= 400:
                mark_failed()
            return response

    if asynchronous:

        async def async_middleware(request: HttpRequest) -> HttpResponse:
            # Keep final ORM bookkeeping in Django's thread-sensitive DB thread;
            # asgiref propagates the boundary to the awaited response and back.
            return await sync_to_async(middleware)(request)

        return async_middleware
    return middleware
