"""Tests for the composed ASGI dispatcher's scope-routing helpers.

:mod:`angee.asgi` wires a :class:`~channels.routing.ProtocolTypeRouter` whose
``http`` arm is :func:`angee.asgi._http_app` (mounted sub-apps by path prefix,
else Django) and whose ``lifespan`` arm is :class:`angee.asgi._Lifespan` (runs
each mount's own ASGI lifespan at server startup). These drive both directly,
the way the serving ASGI server (uvicorn) does.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from angee.asgi import _http_app, _Lifespan


def _recording_app(name: str, sink: list[tuple[str, str]]) -> Any:
    """Return an ASGI app that records ``(name, path)`` for each call."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        sink.append((name, scope["path"]))

    return app


async def _call_http(app: Any, path: str) -> None:
    """Send one minimal HTTP scope through ``app``."""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await app({"type": "http", "path": path}, receive, send)


def test_http_app_without_mounts_is_the_django_app() -> None:
    """With no mounts the HTTP arm is the Django app itself, not a wrapper."""

    django = _recording_app("django", [])
    assert _http_app(django, []) is django


def test_http_app_routes_by_prefix_then_falls_through_to_django() -> None:
    """A path under a mount prefix reaches the mount; anything else reaches Django."""

    seen: list[tuple[str, str]] = []
    django = _recording_app("django", seen)
    mcp = _recording_app("mcp", seen)
    app = _http_app(django, [("/mcp", mcp)])

    asyncio.run(_call_http(app, "/mcp"))
    asyncio.run(_call_http(app, "/mcp/messages"))
    asyncio.run(_call_http(app, "/graphql"))

    assert seen == [("mcp", "/mcp"), ("mcp", "/mcp/messages"), ("django", "/graphql")]


def test_http_app_matches_longest_prefix_first() -> None:
    """A nested mount wins over its parent regardless of declaration order."""

    seen: list[tuple[str, str]] = []
    django = _recording_app("django", seen)
    parent = _recording_app("parent", seen)
    nested = _recording_app("nested", seen)
    app = _http_app(django, [("/a", parent), ("/a/b", nested)])

    asyncio.run(_call_http(app, "/a/b/x"))
    asyncio.run(_call_http(app, "/a/x"))

    assert seen == [("nested", "/a/b/x"), ("parent", "/a/x")]


def _mount(events: list[str], *, fail: bool = False) -> Any:
    """Return a Starlette-shaped app whose ``lifespan_context`` records enter/exit."""

    def lifespan_context(_app: Any) -> Any:
        @contextlib.asynccontextmanager
        async def cm() -> AsyncIterator[None]:
            if fail:
                raise RuntimeError("boom")
            events.append("startup")
            try:
                yield
            finally:
                events.append("shutdown")

        return cm()

    return SimpleNamespace(router=SimpleNamespace(lifespan_context=lifespan_context))


async def _drive_lifespan(lifespan: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feed ``messages`` to a lifespan app and return what it sends back."""

    inbox = list(messages)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return inbox.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await lifespan({"type": "lifespan"}, receive, send)
    return sent


def test_lifespan_enters_and_exits_each_mount() -> None:
    """Startup enters every mount's lifespan; shutdown closes them in reverse."""

    events: list[str] = []
    lifespan = _Lifespan([_mount(events)])

    sent = asyncio.run(
        _drive_lifespan(lifespan, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    )

    assert sent == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]
    assert events == ["startup", "shutdown"]


def test_lifespan_reports_a_startup_failure_to_the_server() -> None:
    """A mount that fails to start surfaces ``lifespan.startup.failed``, not a hang."""

    lifespan = _Lifespan([_mount([], fail=True)])

    sent = asyncio.run(_drive_lifespan(lifespan, [{"type": "lifespan.startup"}]))

    assert sent == [{"type": "lifespan.startup.failed", "message": "boom"}]
