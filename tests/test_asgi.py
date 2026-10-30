"""Angee serving timeout and GraphQL WebSocket origin/auth middleware policy."""

from __future__ import annotations

import asyncio
from pathlib import Path
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
from tests.conftest import make_addon


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
    monkeypatch.setattr(asgi, "optional_addon_module", lambda config, module, **options: SimpleNamespace(
        __name__="routes.asgi", websocket_urlpatterns=lambda: [route],
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

    def contribution_module(config: Any, module: str, **options: Any) -> Any:
        assert module == "asgi"
        if config.name == "routes":
            return SimpleNamespace(__name__="routes.asgi", websocket_urlpatterns=[path("ws", endpoint)])

        def wrap(inner: Any) -> Any:
            async def middleware(scope: Any, receive: Any, send: Any) -> None:
                assert scope["user"].is_anonymous
                seen.append(config.name)
                await inner(scope, receive, send)
            return middleware
        return SimpleNamespace(__name__=f"{config.name}.asgi", websocket_middleware=[wrap])

    monkeypatch.setattr(asgi, "optional_addon_module", contribution_module)

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


@pytest.fixture
def websocket_addon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Expose one native addon to the websocket capability owner."""

    config = make_addon(path=tmp_path)
    monkeypatch.setattr(asgi.apps, "get_app_configs", lambda: [config])
    return config


@pytest.mark.parametrize("declaration", ["websocket_urlpatterns = []", "def websocket_urlpatterns(): return []"])
def test_websocket_routes_accept_static_and_callable_conventions(tmp_path: Path, websocket_addon, declaration: str):
    (tmp_path / "asgi.py").write_text(declaration + "\n")
    assert asgi.websocket_application() is not None


def test_websocket_absent_module_and_unbound_exports_contribute_nothing(tmp_path: Path, websocket_addon):
    assert asgi.websocket_application() is not None
    (tmp_path / "asgi.py").write_text("websocket_urlpatterns: list[object]\nwebsocket_middleware: list[object]\n")
    assert asgi.websocket_application() is not None


def test_websocket_present_module_preserves_internal_import_failure(tmp_path: Path, websocket_addon):
    (tmp_path / "asgi.py").write_text("import missing_angee_test_dependency\n")
    with pytest.raises(ImproperlyConfigured, match="asgi failed to import") as failure:
        asgi.websocket_application()
    assert isinstance(failure.value.__cause__, ModuleNotFoundError)
    assert failure.value.__cause__.name == "missing_angee_test_dependency"


@pytest.mark.parametrize("attribute", ["websocket_urlpatterns", "websocket_middleware"])
@pytest.mark.parametrize("value", ["None", "1", "'bad'", "b'bad'", "{'bad'}", "{'bad': 1}"])
def test_websocket_contributions_reject_nondeterministic_or_non_iterable_values(
    tmp_path: Path, websocket_addon, attribute: str, value: str,
):
    (tmp_path / "asgi.py").write_text(f"{attribute} = {value}\n")
    with pytest.raises(ImproperlyConfigured, match=f"{attribute} must be iterable"):
        asgi.websocket_application()


def test_websocket_routes_validate_callable_results(tmp_path: Path, websocket_addon):
    (tmp_path / "asgi.py").write_text("def websocket_urlpatterns(): return 1\n")
    with pytest.raises(ImproperlyConfigured, match="websocket_urlpatterns must be iterable or callable"):
        asgi.websocket_application()


@pytest.mark.parametrize("declaration, message", [
    ("def websocket_middleware(): return []", "asgi.websocket_middleware must be iterable"),
    ("websocket_middleware = [1]", "asgi.websocket_middleware must contain callables"),
])
def test_websocket_middleware_validates_sequence_and_callable_members(
    tmp_path: Path, websocket_addon, declaration: str, message: str,
):
    (tmp_path / "asgi.py").write_text(declaration + "\n")
    with pytest.raises(ImproperlyConfigured, match=message):
        asgi.websocket_application()
