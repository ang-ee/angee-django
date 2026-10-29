"""Angee serving timeout and GraphQL WebSocket origin/auth middleware policy."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import strawberry
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created
from django.urls import path

from angee.graphql import asgi
from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.server.signals import WEB_STATEMENT_TIMEOUT_UID, connect_web_statement_timeout


def test_only_the_web_entrypoint_bounds_postgresql_statements(settings: Any) -> None:
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

    settings.ANGEE_WEB_STATEMENT_TIMEOUT = 60
    connection_created.disconnect(dispatch_uid=WEB_STATEMENT_TIMEOUT_UID)
    open_connection()  # a worker or command does not register the ASGI receiver
    assert statements == []
    try:
        connect_web_statement_timeout()
        open_connection()
        open_connection("sqlite")
    finally:
        connection_created.disconnect(dispatch_uid=WEB_STATEMENT_TIMEOUT_UID)
    assert statements == [("SELECT set_config('statement_timeout', %s, false)", ["60000"])]

    statements.clear()
    settings.ANGEE_WEB_STATEMENT_TIMEOUT = "0"  # a deployment disabling the bound
    try:
        connect_web_statement_timeout()
        open_connection()
    finally:
        connection_created.disconnect(dispatch_uid=WEB_STATEMENT_TIMEOUT_UID)
    assert statements == []


@pytest.mark.parametrize("value", ["60s", "-5", "nan", None])
def test_an_invalid_web_statement_timeout_fails_at_boot(settings: Any, value: object) -> None:
    """A malformed or negative timeout stops the web app from starting, not every request."""

    settings.ANGEE_WEB_STATEMENT_TIMEOUT = value
    with pytest.raises(ImproperlyConfigured, match="ANGEE_WEB_STATEMENT_TIMEOUT"):
        connect_web_statement_timeout()


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
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr("django.apps.apps.get_app_configs", lambda: [SimpleNamespace(name="routes")])
    monkeypatch.setattr(asgi, "addon_contribution", lambda config, module, attr, **options: (
        [route] if attr == "websocket_urlpatterns" else []
    ))
    application = asgi.websocket_application()

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


@pytest.mark.parametrize("wrapper_names", [(), ("first", "second")])
def test_websocket_addon_wrappers_are_optional_and_run_after_authentication(
    monkeypatch: pytest.MonkeyPatch, settings: Any, wrapper_names: tuple[str, ...],
) -> None:
    """An installed capability wraps an authenticated scope in declared order."""

    settings.DEBUG = True
    settings.ALLOWED_HOSTS = ["localhost"]
    seen: list[str] = []
    addons = [SimpleNamespace(name=name) for name in ("routes", *wrapper_names)]
    monkeypatch.setattr("django.apps.apps.get_app_configs", lambda: addons)

    def contribution(config: Any, module: str, attr: str, **options: Any) -> list[Any]:
        assert module == "asgi"
        if attr == "websocket_urlpatterns":
            return [path("ws", endpoint)] if config.name == "routes" else []
        assert attr == "websocket_middleware"
        if config.name == "routes":
            return []

        def wrap(inner: Any) -> Any:
            async def middleware(scope: Any, receive: Any, send: Any) -> None:
                assert scope["user"].is_anonymous
                seen.append(config.name)
                await inner(scope, receive, send)
            return middleware
        return [wrap]

    monkeypatch.setattr(asgi, "addon_contribution", contribution)

    async def endpoint(scope: Any, receive: Any, send: Any) -> None:
        assert scope["user"].is_anonymous
        seen.append("endpoint")
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        await receive()

    application = asgi.websocket_application()

    async def exercise() -> None:
        client = WebsocketCommunicator(
            application, "/ws", headers=[(b"host", b"localhost"), (b"origin", b"http://localhost")],
        )
        connected, _ = await client.connect()
        assert connected
        await client.disconnect()

    asyncio.run(exercise())
    assert seen == [*wrapper_names, "endpoint"]


