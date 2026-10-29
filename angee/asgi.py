"""Stable ASGI entrypoint for composed Angee runtimes."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, cast

from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope
from django.apps import apps
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_contribution
from angee.middleware import WebsocketOriginValidator
from angee.project import bootstrap
from angee.signals import connect_web_statement_timeout


def _websocket_urlpatterns() -> list[object]:
    """Return WebSocket URL patterns contributed by installed addons."""

    patterns: list[object] = []
    for app_config in apps.get_app_configs():
        patterns.extend(addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True))
    return patterns


def _http_mounts() -> list[tuple[str, ASGI3Application]]:
    """Return the ``(path_prefix, ASGI app)`` HTTP mounts contributed by addons."""

    mounts: list[tuple[str, ASGI3Application]] = []
    for app_config in apps.get_app_configs():
        mounts.extend(
            (prefix, app)
            for prefix, app in addon_contribution(app_config, "asgi", "http_mounts", allow_callable=True)
        )
    return mounts


def _lifespans() -> list[Callable[[], AbstractAsyncContextManager[Any]]]:
    """Return explicit lifespan factories contributed by installed addons."""

    return [
        factory
        for app_config in apps.get_app_configs()
        for factory in addon_contribution(app_config, "asgi", "lifespans", allow_callable=True)
    ]


def _application() -> Any:
    """Build the ASGI application after settings and apps are ready.

    Every database connection the process opens afterwards bounds its
    statements through :func:`angee.signals.bound_web_statements`.

    With no route or lifespan contributions the bare Django app is returned.
    Otherwise a :class:`~channels.routing.ProtocolTypeRouter`
    owns the scope-type switch: ``http`` to the mounted sub-apps or Django,
    ``websocket`` to the channels stack, and ``lifespan`` to :class:`_Lifespan`,
    which enters each addon's explicitly declared lifespan at server startup.
    The serving ASGI server must send the lifespan
    protocol — Angee serves with uvicorn (see ``docs/stack.md``).

    Every WebSocket uses Django session authentication and Origin validation:
    the request's own scheme/host, CSRF_TRUSTED_ORIGINS, and loopback origins in
    DEBUG are trusted. Missing Origins are refused, even with ALLOWED_HOSTS=['*'].
    """

    # Django's stubs describe ASGI messages as Mapping rather than asgiref's
    # protocol TypedDicts; the native handler implements the same ASGI 3 contract.
    django_asgi_app = cast(ASGI3Application, get_asgi_application())
    connect_web_statement_timeout()
    websocket_patterns = _websocket_urlpatterns()
    http_mounts = _http_mounts()
    lifespans = _lifespans()
    if not websocket_patterns and not http_mounts and not lifespans:
        return django_asgi_app

    from channels.routing import ProtocolTypeRouter

    mapping: dict[str, ASGI3Application] = {
        "http": _http_app(django_asgi_app, http_mounts),
        "lifespan": _Lifespan(lifespans),
    }
    if websocket_patterns:
        mapping["websocket"] = websocket_application(websocket_patterns)
    return ProtocolTypeRouter(mapping)


def websocket_application(patterns: list[object]) -> ASGI3Application:
    """Compose the shared cookie/Origin and attribution boundaries for addon WebSocket routes.

    Installed addons contribute wrappers through ``asgi.websocket_middleware``.
    Authentication and origin validation remain the host boundary.
    """

    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter

    application = URLRouter(patterns)
    wrappers = [
        wrapper
        for app_config in apps.get_app_configs()
        for wrapper in addon_contribution(app_config, "asgi", "websocket_middleware")
    ]
    for wrapper in reversed(wrappers):
        if not callable(wrapper):
            raise ImproperlyConfigured("asgi.websocket_middleware must contain callables")
        application = wrapper(application)
    return WebsocketOriginValidator(AuthMiddlewareStack(application))


def _http_app(django_app: ASGI3Application, http_mounts: list[tuple[str, ASGI3Application]]) -> ASGI3Application:
    """Return the HTTP app: mounted sub-apps by path prefix, else Django.

    With no mounts this is just the Django app. Otherwise mounts are matched
    longest-prefix-first (so a nested mount wins over its parent) and the matched
    app receives the unchanged scope — its own route sits at the mount prefix, so
    no path stripping is needed. Prefixes are absolute paths with optional trailing
    slashes; duplicate normalized prefixes fail at boot. A root mount matches every
    path. Any unmatched path falls through to Django.
    """

    if not http_mounts:
        return django_app
    normalized: dict[str, ASGI3Application] = {}
    for prefix, app in http_mounts:
        if not isinstance(prefix, str) or not prefix.startswith("/"):
            raise ImproperlyConfigured(f"ASGI HTTP mount prefix must be an absolute path, got {prefix!r}.")
        prefix = prefix.rstrip("/") or "/"
        if prefix in normalized:
            raise ImproperlyConfigured(f"Duplicate ASGI HTTP mount prefix {prefix!r}.")
        normalized[prefix] = app
    mounts = tuple(sorted(normalized.items(), key=lambda mount: len(mount[0]), reverse=True))

    async def http_app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        path = str(scope.get("path", ""))
        for prefix, app in mounts:
            if prefix == "/" or path == prefix or path.startswith(f"{prefix}/"):
                await app(scope, receive, send)
                return
        await django_app(scope, receive, send)

    return http_app


class _Lifespan:
    """Enter declared addon lifespans during the server's startup and shutdown.

    Each process enters fresh contexts in app order and unwinds them in reverse
    order. Startup failures are reported to the server; cancellation also closes
    every retained context.
    """

    def __init__(self, factories: list[Callable[[], AbstractAsyncContextManager[Any]]]) -> None:
        self._factories = factories
        self._exit_stack: AsyncExitStack | None = None

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        try:
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    try:
                        async with AsyncExitStack() as stack:
                            for factory in self._factories:
                                await stack.enter_async_context(factory())
                            self._exit_stack = stack.pop_all()
                    except Exception as error:  # reported to the server, not swallowed
                        await send({"type": "lifespan.startup.failed", "message": str(error)})
                        return
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    if self._exit_stack is not None:
                        stack, self._exit_stack = self._exit_stack, None
                        await stack.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        finally:
            if self._exit_stack is not None:
                stack, self._exit_stack = self._exit_stack, None
                await stack.aclose()


bootstrap()
application = _application()
"""ASGI application for the composed Angee runtime."""
