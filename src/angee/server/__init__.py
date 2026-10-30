"""Optional HTTP serving capability; imports and app population start no server."""

from __future__ import annotations

from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope


async def application(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    """Load the configured native application only when an ASGI server enters it."""

    from angee.server.asgi import application as native_application

    await native_application(scope, receive, send)
