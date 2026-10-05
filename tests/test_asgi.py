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

import pytest
import strawberry
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.db.backends.signals import connection_created
from django.urls import path

from angee import asgi
from angee.asgi import _http_app, _Lifespan, _run_boot_hooks
from angee.graphql.consumers import AngeeGraphQLWSConsumer


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


def test_run_boot_hooks_dispatches_addon_asgi_contributions(monkeypatch: Any) -> None:
    """Core ASGI boot runs callable hooks without knowing addon implementations."""

    addon = SimpleNamespace(name="example.addon")
    calls: list[str] = []
    monkeypatch.setattr("django.apps.apps.get_app_configs", lambda: [addon])

    def contribution(app_config: Any, module_name: str, attr: str) -> list[Any]:
        assert app_config is addon
        assert (module_name, attr) == ("asgi", "boot_hooks")
        return [lambda: calls.append("boot")]

    monkeypatch.setattr("angee.addons.addon_contribution", contribution)

    _run_boot_hooks()

    assert calls == ["boot"]


def test_only_the_web_entrypoint_bounds_postgresql_statements(monkeypatch: Any, settings: Any) -> None:
    """Web-process PostgreSQL connections carry the timeout; workers and commands keep the server's."""

    statements: list[tuple[str, list[str]]] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def execute(self, sql: str, params: list[str]) -> None:
            statements.append((sql, params))

    def open_connection(vendor: str = "postgresql") -> None:
        connection_created.send(sender=None, connection=SimpleNamespace(vendor=vendor, cursor=Cursor))

    monkeypatch.setattr(asgi, "_run_boot_hooks", lambda: None)
    monkeypatch.setattr(asgi, "_websocket_urlpatterns", list)
    monkeypatch.setattr(asgi, "_http_mounts", list)
    open_connection()  # a Celery worker or management command never builds the web app
    assert statements == []
    try:
        asgi._application()
        open_connection()
        open_connection("sqlite")
    finally:
        connection_created.disconnect(dispatch_uid=asgi.WEB_STATEMENT_TIMEOUT_UID)
    assert statements == [("SELECT set_config('statement_timeout', %s, false)", ["60000"])]

    statements.clear()
    settings.ANGEE_WEB_STATEMENT_TIMEOUT = "0"  # a deployment disabling the bound
    try:
        asgi._application()
        open_connection()
    finally:
        connection_created.disconnect(dispatch_uid=asgi.WEB_STATEMENT_TIMEOUT_UID)
    assert statements == []


@pytest.mark.parametrize("value", ["60s", "-5", "nan", None])
def test_an_invalid_web_statement_timeout_fails_at_boot(monkeypatch: Any, settings: Any, value: object) -> None:
    """A malformed or negative timeout stops the web app from starting, not every request."""

    from django.core.exceptions import ImproperlyConfigured

    monkeypatch.setattr(asgi, "_run_boot_hooks", lambda: None)
    settings.ANGEE_WEB_STATEMENT_TIMEOUT = value
    with pytest.raises(ImproperlyConfigured, match="ANGEE_WEB_STATEMENT_TIMEOUT"):
        asgi._application()


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

    sent = asyncio.run(_drive_lifespan(lifespan, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]))

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


@pytest.mark.parametrize(
    ("host", "origin", "debug", "accepted"),
    [
        ("localhost:5173", "http://localhost:5173", False, True),
        ("localhost:5173", "https://trusted.example", False, True),
        ("localhost:5173", "http://localhost:5174", False, False),
        ("app.example", "http://sibling.app.example", False, False),
        ("app.example", None, False, False),
        ("app.example", "http://localhost:5174", True, True),
        ("app.example", "http://127.0.0.1:8000", True, True),
        ("app.example", "http://[::1]:8000", True, True),
        ("app.example", None, True, False),
        ("app.example", "https://untrusted.example", True, False),
    ],
)
def test_shared_router_validates_graphql_websocket_origins(
    settings: Any,
    host: str,
    origin: str | None,
    debug: bool,
    accepted: bool,
) -> None:
    """Wildcard allowed hosts never bypass the shared cookie-write Origin rule."""

    @strawberry.type
    class Query:
        @strawberry.field
        def ready(self) -> bool:
            return True

    settings.ALLOWED_HOSTS = ["*"]
    settings.CSRF_TRUSTED_ORIGINS = ["https://trusted.example"]
    settings.DEBUG = debug
    route = path("graphql/test/", AngeeGraphQLWSConsumer.as_asgi(schema=strawberry.Schema(query=Query)))
    application = asgi.websocket_application([route])

    async def scenario() -> None:
        headers = [(b"host", host.encode())]
        if origin:
            headers.append((b"origin", origin.encode()))
        socket = WebsocketCommunicator(
            application,
            "/graphql/test/",
            subprotocols=["graphql-transport-ws"],
            headers=headers,
        )
        socket.scope["user"] = AnonymousUser()
        connected, _ = await socket.connect()
        assert connected is accepted
        if connected:
            await socket.send_json_to({"type": "connection_init"})
            assert await socket.receive_json_from() == {"type": "connection_ack"}
        await socket.disconnect()

    asyncio.run(scenario())
