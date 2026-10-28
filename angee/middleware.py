"""Host ASGI middleware composed around native Django and Channels owners."""

from __future__ import annotations

from io import BytesIO

from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope


class WebsocketOriginValidator:
    """Validate browser websocket origins using native request and Channels policy."""

    def __init__(self, application: ASGI3Application) -> None:
        self.application = application

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        from channels.security.websocket import OriginValidator
        from django.conf import settings
        from django.core.exceptions import DisallowedHost
        from django.core.handlers.asgi import ASGIRequest

        request = ASGIRequest(
            {**scope, "method": "GET", "scheme": "https" if scope.get("scheme") in ("https", "wss") else "http"},
            BytesIO(),
        )
        trusted = [origin.replace("://*.", "://.") for origin in settings.CSRF_TRUSTED_ORIGINS if origin != "*"]
        try:
            trusted.append(f"{request.scheme}://{request.get_host()}")
        except DisallowedHost:
            await OriginValidator(self.application, [])(scope, receive, send)
            return
        if settings.DEBUG:
            trusted.extend(("localhost", "127.0.0.1", "[::1]"))
        await OriginValidator(self.application, trusted)(scope, receive, send)
