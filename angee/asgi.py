"""Stable ASGI entrypoint for composed Angee runtimes."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from functools import partial
from io import BytesIO
from typing import Any, cast

from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope
from django.core.exceptions import ImproperlyConfigured

from angee.project import bootstrap


def _websocket_urlpatterns() -> list[object]:
    """Return WebSocket URL patterns contributed by installed addons."""

    # Deferred: tests import routing helpers before Django's app registry is set
    # up; the composed application path imports this only during the lazy build.
    from django.apps import apps

    from angee.addons import addon_contribution

    patterns: list[object] = []
    for app_config in apps.get_app_configs():
        patterns.extend(addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True))
    return patterns


def _http_mounts() -> list[tuple[str, ASGI3Application]]:
    """Return the ``(path_prefix, ASGI app)`` HTTP mounts contributed by addons."""

    # Deferred for the same Django app-loading reason as
    # :func:`_websocket_urlpatterns`.
    from django.apps import apps

    from angee.addons import addon_contribution

    mounts: list[tuple[str, ASGI3Application]] = []
    for app_config in apps.get_app_configs():
        mounts.extend(
            (prefix, app)
            for prefix, app in addon_contribution(app_config, "asgi", "http_mounts", allow_callable=True)
        )
    return mounts


def _lifespans() -> list[Callable[[], AbstractAsyncContextManager[Any]]]:
    """Return explicit lifespan factories contributed by installed addons."""

    from django.apps import apps

    from angee.addons import addon_contribution

    return [
        factory
        for app_config in apps.get_app_configs()
        for factory in addon_contribution(app_config, "asgi", "lifespans", allow_callable=True)
    ]


def _run_boot_hooks() -> None:
    """Run ASGI boot hooks contributed by installed addons after app population."""

    from django.apps import apps

    from angee.addons import addon_contribution

    for app_config in apps.get_app_configs():
        hooks = addon_contribution(app_config, "asgi", "boot_hooks")
        for hook in hooks:
            if not callable(hook):
                raise ImproperlyConfigured(f"{app_config.name}.asgi.boot_hooks must contain callables")
            hook()


WEB_STATEMENT_TIMEOUT_UID = "angee.asgi.web_statement_timeout"


def _web_statement_timeout_ms() -> int:
    """Read ``ANGEE_WEB_STATEMENT_TIMEOUT`` once, at boot, in milliseconds.

    The setting is seconds; 0 disables the bound. Anything that is not zero or a
    positive finite number fails at boot instead of on every connection.
    """

    from django.conf import settings

    raw = settings.ANGEE_WEB_STATEMENT_TIMEOUT
    try:
        seconds = float(raw)
    except TypeError, ValueError:
        raise ImproperlyConfigured(f"ANGEE_WEB_STATEMENT_TIMEOUT must be a number of seconds, not {raw!r}.") from None
    if seconds < 0 or not math.isfinite(seconds):
        raise ImproperlyConfigured(
            f"ANGEE_WEB_STATEMENT_TIMEOUT must be zero or a positive number of seconds, not {raw!r}."
        )
    return 0 if seconds == 0 else max(1, round(seconds * 1000))


def _bound_web_statements(*, connection: Any, timeout_ms: int, **_signal: Any) -> None:
    """Bound each PostgreSQL statement a web-process connection runs.

    The bound caps every statement a request, subscription or mounted app
    issues; it applies per statement, not per request. Only
    :func:`_application` connects this receiver: Celery workers, migrations,
    imports and other management commands never import this entrypoint and keep
    the server's own timeout.
    """

    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('statement_timeout', %s, false)", [str(timeout_ms)])


def _application() -> Any:
    """Build the ASGI application after settings and apps are ready.

    Every database connection the process opens afterwards bounds its
    statements through :func:`_bound_web_statements`.

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

    # Deferred: importing ``django.core.asgi`` at module collection time pulls on
    # Django internals before pytest-django owns setup.
    from django.core.asgi import get_asgi_application
    from django.db.backends.signals import connection_created

    timeout_ms = _web_statement_timeout_ms()
    # Django's stubs describe ASGI messages as Mapping rather than asgiref's
    # protocol TypedDicts; the native handler implements the same ASGI 3 contract.
    django_asgi_app = cast(ASGI3Application, get_asgi_application())
    if timeout_ms:
        connection_created.connect(
            partial(_bound_web_statements, timeout_ms=timeout_ms),
            weak=False,
            dispatch_uid=WEB_STATEMENT_TIMEOUT_UID,
        )
    _run_boot_hooks()
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
    from django.apps import apps

    from angee.addons import addon_contribution

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
    return _WebsocketOriginValidator(AuthMiddlewareStack(application))


class _WebsocketOriginValidator:
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


class _LazyApplication:
    """Build the composed ASGI app on first use, not module import.

    Tests import this module's routing helpers without a composed runtime. The
    server-facing ``application`` object is still a normal ASGI callable, but the
    expensive Django/app/addon composition happens only when a server dispatches
    a scope. With uvicorn workers, each process builds its own application and
    keeps its own in-memory state; shared fanout and cache state belong in Redis.
    """

    def __init__(self) -> None:
        self._wrapped: ASGI3Application | None = None

    def _app(self) -> ASGI3Application:
        if self._wrapped is None:
            bootstrap()
            self._wrapped = cast(ASGI3Application, _application())
        return self._wrapped

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        await self._app()(scope, receive, send)


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


application = _LazyApplication()
"""ASGI application for the composed Angee runtime."""
