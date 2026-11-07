"""Optional HTTP serving capability; imports and app population start no server."""

from __future__ import annotations

from asgiref.sync import sync_to_async
from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope

READINESS_ROUTE = "health/ready"
"""The readiness route stack probes poll; request profiling never records it."""

_native_application: ASGI3Application | None = None


async def application(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    """Load the configured native application when an ASGI server first enters it.

    Its construction runs Django setup and collects addon contributions, which may
    read the database, so it runs once on Django's thread-sensitive worker, off the
    event loop. Later calls reuse the loaded application.
    """

    global _native_application
    if _native_application is None:
        _native_application = await sync_to_async(_load_native_application, thread_sensitive=True)()
    await _native_application(scope, receive, send)


def _load_native_application() -> ASGI3Application:
    from angee.server.asgi import application as native_application

    return native_application
