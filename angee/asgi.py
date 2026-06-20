"""Stable ASGI entrypoint for composed Angee runtimes."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.asgi import get_asgi_application

from angee.addons import addon_contribution
from angee.paths import resolve_path

_PROJECT_DIR_ENV = "ANGEE_PROJECT_DIR"


def _project_dir() -> Path | None:
    """Return the project root for direct ASGI imports, when discoverable."""

    configured = os.environ.get(_PROJECT_DIR_ENV)
    if configured:
        return resolve_path(configured)
    for parent in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        if (parent / "settings.yaml").exists() or (parent / "settings.py").exists():
            return parent
    return None


def _bootstrap() -> None:
    """Point Django at Angee's composed settings module."""

    project_dir = _project_dir()
    if project_dir is not None:
        os.environ.setdefault(_PROJECT_DIR_ENV, str(project_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")


def _websocket_urlpatterns() -> list[object]:
    """Return WebSocket URL patterns contributed by installed addons."""

    patterns: list[object] = []
    for app_config in apps.get_app_configs():
        patterns.extend(addon_contribution(app_config, "asgi", "websocket_urlpatterns", allow_callable=True))
    return patterns


def _http_mounts() -> list[tuple[str, Any]]:
    """Return the ``(path_prefix, ASGI app)`` HTTP mounts contributed by addons."""

    mounts: list[tuple[str, Any]] = []
    for app_config in apps.get_app_configs():
        mounts.extend(
            (str(prefix), app)
            for prefix, app in addon_contribution(app_config, "asgi", "http_mounts", allow_callable=True)
        )
    return mounts


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _emit_dev_sdl() -> None:
    """Regenerate the generated GraphQL SDL once per dev-serve worker boot.

    Only the dev ``runserver`` path sets ``ANGEE_DEV_SDL=1`` (a production serve
    leaves the runtime image untouched), so this is a no-op everywhere else. It
    runs at ASGI-import time, after :func:`get_asgi_application` has populated the
    app registry, so the concrete models the SDL introspects are imported. The
    GraphQL owner is imported lazily so a production serve never loads strawberry
    here. Management commands (``schema --check``/``angee build --check``/tests)
    never import this module, so their drift gates stay live.
    """

    if os.environ.get("ANGEE_DEV_SDL") != "1":
        return
    # Deferred: keep the GraphQL/strawberry stack off the no-op production-serve
    # import path (the flag is set only by the dev `runserver` override).
    from angee.graphql.sdl import GraphQLSdl

    GraphQLSdl.from_discovery().emit_if_stale()


def _application() -> Any:
    """Build the ASGI application after settings and apps are ready.

    With no WebSocket or HTTP-mount contributions the bare Django app is returned
    (the common, non-MCP case). Otherwise a :class:`~channels.routing.ProtocolTypeRouter`
    owns the scope-type switch: ``http`` to the mounted sub-apps or Django,
    ``websocket`` to the channels stack, and ``lifespan`` to :class:`_Lifespan`,
    which runs each mount's own ASGI lifespan (the FastMCP session manager's task
    group) at server startup. The serving ASGI server must send the lifespan
    protocol — Angee serves with uvicorn (see ``docs/stack.md``).
    """

    django_asgi_app = get_asgi_application()
    _emit_dev_sdl()
    websocket_patterns = _websocket_urlpatterns()
    http_mounts = _http_mounts()
    if not websocket_patterns and not http_mounts:
        return django_asgi_app

    from channels.routing import ProtocolTypeRouter

    mapping: dict[str, ASGIApp] = {
        "http": _http_app(django_asgi_app, http_mounts),
        "lifespan": _Lifespan([app for _prefix, app in http_mounts]),
    }
    if websocket_patterns:
        from channels.auth import AuthMiddlewareStack
        from channels.routing import URLRouter

        mapping["websocket"] = AuthMiddlewareStack(URLRouter(websocket_patterns))
    return ProtocolTypeRouter(mapping)


def _http_app(django_app: ASGIApp, http_mounts: list[tuple[str, ASGIApp]]) -> ASGIApp:
    """Return the HTTP app: mounted sub-apps by path prefix, else Django.

    With no mounts this is just the Django app. Otherwise mounts are matched
    longest-prefix-first (so a nested mount wins over its parent) and the matched
    app receives the unchanged scope — its own route sits at the mount prefix, so
    no path stripping is needed. Any unmatched path falls through to Django.
    """

    if not http_mounts:
        return django_app
    mounts = tuple(sorted(http_mounts, key=lambda mount: len(mount[0]), reverse=True))

    async def http_app(scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        for prefix, app in mounts:
            if path == prefix or path.startswith(f"{prefix}/"):
                await app(scope, receive, send)
                return
        await django_app(scope, receive, send)

    return http_app


def _mount_lifespan(app: Any) -> Any:
    """Return a mounted Starlette app's lifespan context manager, or ``None``.

    A StreamableHTTP app (FastMCP) carries ``session_manager.run()`` as its
    Starlette lifespan; entering it opens the manager's task group before any
    request. ``None`` for a mount with no lifespan.
    """

    router = getattr(app, "router", None)
    lifespan_context = getattr(router, "lifespan_context", None)
    return lifespan_context(app) if lifespan_context is not None else None


class _Lifespan:
    """Run the mounted sub-apps' ASGI lifespans for the life of the process.

    A FastMCP StreamableHTTP app must have its session manager running before it
    serves a request. This drives the ASGI lifespan protocol the serving server
    (uvicorn) sends: it enters every mount's lifespan at ``startup`` and holds the
    task groups open via a retained ``AsyncExitStack`` until ``shutdown``. A
    startup failure is reported to the server, never swallowed.
    """

    def __init__(self, mounts: list[Any]) -> None:
        self._mounts = mounts
        self._exit_stack: AsyncExitStack | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    stack = AsyncExitStack()
                    for app in self._mounts:
                        lifespan = _mount_lifespan(app)
                        if lifespan is not None:
                            await stack.enter_async_context(lifespan)
                    self._exit_stack = stack
                except Exception as error:  # reported to the server, not swallowed
                    await send({"type": "lifespan.startup.failed", "message": str(error)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._exit_stack is not None:
                    await self._exit_stack.aclose()
                    self._exit_stack = None
                await send({"type": "lifespan.shutdown.complete"})
                return


_bootstrap()
application = _application()
"""ASGI application for the composed Angee runtime."""
