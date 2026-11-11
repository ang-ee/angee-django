"""Tests for the operator daemon-bridge addon."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

import httpx2
import pytest
import strawberry
from django.core.cache import cache
from django.core.management import call_command
from rebac import ObjectRef, RelationshipTuple, SubjectRef
from rebac.schema import ConstBinding, parse_zed
from rebac.testing import install_schema

from angee.integrate.errors import RequestNotSentError
from angee.integrate.testing.http import mock_transport
from angee.operator import daemon as daemon_module
from angee.operator import schema as operator_schema
from angee.operator.daemon import (
    ConnectionToken,
    OperatorDaemon,
    OperatorDaemonConflict,
    OperatorDaemonError,
    OperatorDaemonNotFound,
    OperatorInstanceKind,
    WorkspaceStatus,
)
from angee.operator.management.commands import operator_schema as operator_schema_command

_CONNECTION_QUERY = "{ operatorConnection { endpoint token restartJob } }"
_ACTOR = SubjectRef.of("auth/user", "abc")


@pytest.fixture(autouse=True)
def _clear_operator_token_cache() -> Iterator[None]:
    """Keep daemon token-cache assertions independent within this module."""

    cache.clear()
    yield
    cache.clear()


def test_daemon_error_surfaces_the_response_body() -> None:
    """A daemon HTTP error reports its body (JSON ``error`` field, else text), not a bare status."""

    def message(status: int, body: bytes) -> str:
        return str(OperatorDaemonError.from_response("POST", "http://op/workspaces", status, body))

    assert message(500, b'{"error": "secret \\"x\\" is not resolved"}') == (
        'operator POST workspaces: HTTP 500: secret "x" is not resolved'
    )
    assert message(400, b'{"reason": "bad input"}') == "operator POST workspaces: HTTP 400: bad input"
    assert message(503, b"upstream down") == "operator POST workspaces: HTTP 503: upstream down"
    assert message(502, b"") == "operator POST workspaces: HTTP 502"


def test_daemon_error_types_a_conflict_with_the_daemon_reported_instance() -> None:
    """A 409 carries the daemon's own ``kind``/``name``; the client never parses the message."""

    error = OperatorDaemonError.from_response(
        "POST",
        "http://op/workspaces",
        409,
        b'{"kind": "workspace", "name": "demo-ws", "reason": "already exists",'
        b' "error": "workspace demo-ws conflicts: already exists"}',
    )

    assert isinstance(error, OperatorDaemonConflict)
    assert (error.status_code, error.kind, error.name) == (409, OperatorInstanceKind.WORKSPACE, "demo-ws")
    assert str(error) == "operator POST workspaces: HTTP 409: workspace demo-ws conflicts: already exists"

    # A stale-etag file write is a 409 too, but names no workspace or service.
    for body in (b"stale etag", b'{"kind": "file", "name": "settings.yaml", "error": "stale etag"}'):
        unclassified = OperatorDaemonError.from_response("PUT", "http://op/files", 409, body)
        assert isinstance(unclassified, OperatorDaemonConflict)
        assert unclassified.kind is None


def _daemon() -> OperatorDaemon:
    """Return a daemon client bound to a server-side base and the admin bearer."""

    return OperatorDaemon(
        endpoint="http://op/graphql",
        server_base="http://op",
        admin_bearer="admin",
        scope=(),
        ttl="1h",
    )


def _answer(status: int, body: bytes) -> Callable[[httpx2.Request], httpx2.Response]:
    """Answer every daemon REST call with ``status`` and ``body``."""

    return lambda request: httpx2.Response(status, content=body)


MINTED_BODY = b'{"token": "ok", "expires_at": "2099-01-01T00:00:00Z"}'


def _urls(requests: list[httpx2.Request]) -> list[str]:
    return [str(request.url) for request in requests]


def test_daemon_request_raises_typed_conflict() -> None:
    """A create the daemon refuses with 409 raises the typed conflict from the REST call."""

    body = b'{"kind": "service", "name": "agent-demo", "error": "service agent-demo conflicts"}'
    with mock_transport(_answer(409, body)), pytest.raises(OperatorDaemonConflict) as raised:
        _daemon().create_service(template="services/agent", workspace="demo", inputs={})

    assert (raised.value.kind, raised.value.name) == (OperatorInstanceKind.SERVICE, "agent-demo")


def test_workspace_status_reads_template_inputs_and_mounting_services() -> None:
    """The status read keeps the template ref, the recorded inputs, and the services (not jobs) mounting it."""

    body = json.dumps(
        {
            "name": "demo ws",
            "template": "workspaces/agent-default",
            "inputs": {"agent_name": "Demo", "instructions": "Hi."},
            "mounted_by": [
                {"kind": "service", "name": "agent-demo", "field": "mounts", "value": "workspace://demo ws"},
                {"kind": "service", "name": "agent-demo", "field": "workdir", "value": "workspace://demo ws/"},
                {"kind": "job", "name": "backup", "field": "mounts", "value": "workspace://demo ws"},
            ],
        }
    ).encode()
    with mock_transport(_answer(200, body)) as requests:
        status = _daemon().workspace_status("demo ws")

    assert status == WorkspaceStatus(
        name="demo ws",
        template="workspaces/agent-default",
        inputs={"agent_name": "Demo", "instructions": "Hi."},
        services=("agent-demo",),
    )
    assert _urls(requests) == ["http://op/workspaces/demo%20ws/status"]


def test_service_status_reads_the_service_listing() -> None:
    """The daemon has no single-service read; the status comes from its service listing."""

    body = b'{"nodes": [{"name": "agent-demo", "runtime": "container", "status": "exited"}], "total_count": 1}'
    with mock_transport(_answer(200, body)) as requests:
        assert _daemon().service_status("agent-demo") == "exited"
        assert _daemon().service_status("agent-other") is None
    assert _urls(requests) == ["http://op/services", "http://op/services"]


@pytest.mark.parametrize(
    ("call", "body"),
    [
        (lambda daemon: daemon.destroy_service("svc"), b'{"kind": "service", "name": "svc", "error": "gone"}'),
        (lambda daemon: daemon.destroy_workspace("ws"), b'{"kind": "workspace", "name": "ws", "error": "gone"}'),
        (lambda daemon: daemon.workspace_status("ws"), b'{"kind": "workspace", "name": "ws", "error": "gone"}'),
    ],
)
def test_an_instance_is_absent_only_when_the_not_found_names_it(
    call: Callable[[OperatorDaemon], object], body: bytes,
) -> None:
    """A not-found naming the asked-for instance means it is gone: destroyed already, or no status."""

    with mock_transport(_answer(404, body)):
        assert call(_daemon()) is None


@pytest.mark.parametrize(
    "body",
    [
        b'{"error": "service \\"svc\\" is not declared"}',
        b"404 page not found",
        b'{"kind": "service", "name": "other-svc", "error": "gone"}',
        b'{"kind": "workspace", "name": "svc", "error": "gone"}',
    ],
)
def test_a_not_found_that_names_another_instance_still_raises(body: bytes) -> None:
    """A plain 404 — a proxy, a mis-mounted URL — or one about another instance is never read as destroyed."""

    with mock_transport(_answer(404, body)), pytest.raises(OperatorDaemonNotFound) as raised:
        _daemon().destroy_service("svc")

    assert raised.value.status_code == 404
    assert str(raised.value).startswith("operator POST destroy: HTTP 404")


def test_daemon_request_uses_the_shared_integrate_http_client() -> None:
    """Operator REST calls ride the shared pinned HTTP owner, allowing local daemon addresses."""

    with mock_transport(_answer(200, b'{"ok": true}')) as requests:
        assert _daemon()._request("POST", "http://op/workspaces", {"name": "demo"}, timeout=7) == {"ok": True}

    [request] = requests
    assert (request.method, str(request.url)) == ("POST", "http://op/workspaces")
    assert (request.headers["Content-Type"], request.headers["Authorization"]) == ("application/json", "Bearer admin")
    assert json.loads(request.content) == {"name": "demo"}
    assert request.extensions["allow_private"] is True
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 7)


def test_resolve_template_ref_reads_collection_envelope() -> None:
    """The daemon's template REST list is a collection envelope, not a bare array."""

    templates = {
        "nodes": [
            {"name": "agent-default", "kind": "service", "ref": "services/wrong-kind"},
            {"name": "agent-default", "kind": "workspace", "ref": "workspaces/agent-default"},
        ],
        "total_count": 2,
    }
    with mock_transport(lambda request: httpx2.Response(200, json=templates)) as requests:
        assert _daemon().resolve_template_ref(name="agent-default", kind="workspace") == "workspaces/agent-default"
    assert [(request.method, str(request.url)) for request in requests] == [("GET", "http://op/templates")]


def test_file_tools_call_the_files_api_and_carry_the_etag() -> None:
    """read_file/write_file hit ``/files?source=&path=`` carrying the etag."""

    def files(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, json={
                "source": "app", "path": "settings.yaml", "content": "INSTALLED_APPS: []\n", "etag": "e1",
            })
        assert request.method == "PUT"
        return httpx2.Response(200, json={"source": "app", "path": "settings.yaml", "etag": "e2"})

    daemon = _daemon()
    with mock_transport(files) as requests:
        remote = daemon.read_file("app", "settings.yaml")
        assert (remote.content, remote.etag) == ("INSTALLED_APPS: []\n", "e1")
        assert daemon.write_file("app", "settings.yaml", "INSTALLED_APPS: [x]\n", "e1") == "e2"

    for request in requests:
        assert request.url.path == "/files" and dict(request.url.params) == {"source": "app", "path": "settings.yaml"}
    assert json.loads(requests[1].content) == {"content": "INSTALLED_APPS: [x]\n", "etag": "e1"}
    assert [request.method for request in requests] == ["GET", "PUT"]


# --- endpoint resolution ------------------------------------------------------
#
# The daemon resolves from Django settings only. Dev stack env and project YAML
# are normalized by ``the generated settings module`` before apps read configuration.


def test_endpoint_defaults_to_same_origin_proxy() -> None:
    """With nothing configured the endpoint is the CORS-free proxy default."""

    assert OperatorDaemon.from_settings().endpoint == "/operator/graphql"


def test_endpoint_full_setting_wins_without_doubling_graphql(
    settings: pytest.FixtureRequest,
) -> None:
    """A full endpoint is returned verbatim, not re-suffixed."""

    settings.ANGEE_OPERATOR_GRAPHQL_ENDPOINT = "http://localhost:9000/graphql"

    assert OperatorDaemon.from_settings().endpoint == "http://localhost:9000/graphql"


def test_endpoint_base_url_gains_one_graphql_suffix(
    settings: pytest.FixtureRequest,
) -> None:
    """A base URL is suffixed with a single ``/graphql``."""

    settings.ANGEE_OPERATOR_GRAPHQL_ENDPOINT = None
    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"

    assert OperatorDaemon.from_settings().endpoint == "http://localhost:9000/graphql"


def test_endpoint_keeps_default_proxy_with_an_internal_daemon_url(settings) -> None:
    """Configuring server transport must not expose its hostname to the browser."""

    settings.ANGEE_OPERATOR_URL = "http://daemon:9010"
    daemon = OperatorDaemon.from_settings()

    assert daemon.endpoint == "/operator/graphql"
    assert daemon.server_base == "http://daemon:9010"


# --- admin bearer -------------------------------------------------------------


def test_admin_bearer_prefers_setting(
    settings: pytest.FixtureRequest,
) -> None:
    """A configured setting is the resolved admin bearer."""

    settings.ANGEE_OPERATOR_TOKEN = "from-settings"

    assert OperatorDaemon.from_settings().admin_bearer == "from-settings"


def test_admin_bearer_absent_is_none() -> None:
    """No configured bearer resolves to ``None``."""

    assert OperatorDaemon.from_settings().admin_bearer is None


# --- minting ------------------------------------------------------------------


def test_mint_token_posts_actor_scope_ttl_and_returns_token(settings: pytest.FixtureRequest) -> None:
    """A configured bridge mints over the admin bearer and returns the token."""

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    settings.ANGEE_OPERATOR_TOKEN_SCOPE = ["service:read"]
    settings.ANGEE_OPERATOR_TOKEN_TTL = "30m"

    with mock_transport(_answer(200, b'{"token": "minted-abc", "expires_at": "2099-01-01T00:00:00Z"}')) as requests:
        assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == (
            ConnectionToken("minted-abc", datetime(2099, 1, 1, tzinfo=timezone.utc))
        )
    [request] = requests
    assert str(request.url) == "http://localhost:9000/tokens/mint"
    assert json.loads(request.content) == {"actor": "auth/user:abc", "scope": ["service:read"], "ttl": "30m"}
    assert request.headers["Authorization"] == "Bearer admin-bearer"


def test_mint_token_derives_host_from_full_graphql_endpoint(
    settings: pytest.FixtureRequest,
) -> None:
    """The mint host is derived from a full GraphQL endpoint when that is all that is set."""

    settings.ANGEE_OPERATOR_GRAPHQL_ENDPOINT = "http://daemon:9000/graphql"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    with mock_transport(_answer(200, MINTED_BODY)) as requests:
        assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == (
            ConnectionToken("ok", datetime(2099, 1, 1, tzinfo=timezone.utc))
        )
    assert _urls(requests) == ["http://daemon:9000/tokens/mint"]


def test_mint_token_preserves_mount_prefix(
    settings: pytest.FixtureRequest,
) -> None:
    """A daemon behind a path prefix mints under that prefix, not the bare host.

    Otherwise the admin bearer would POST to a sibling service on the same origin
    (``https://host/tokens/mint`` instead of ``https://host/operator/...``).
    """

    settings.ANGEE_OPERATOR_URL = "https://host/operator"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    with mock_transport(_answer(200, MINTED_BODY)) as requests:
        assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == (
            ConnectionToken("ok", datetime(2099, 1, 1, tzinfo=timezone.utc))
        )
    assert _urls(requests) == ["https://host/operator/tokens/mint"]


def test_mint_token_strips_only_trailing_graphql_from_prefixed_endpoint(
    settings: pytest.FixtureRequest,
) -> None:
    """A prefixed GraphQL endpoint keeps the prefix, dropping only ``/graphql``."""

    settings.ANGEE_OPERATOR_GRAPHQL_ENDPOINT = "https://host/operator/graphql"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    with mock_transport(_answer(200, MINTED_BODY)) as requests:
        assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == (
            ConnectionToken("ok", datetime(2099, 1, 1, tzinfo=timezone.utc))
        )
    assert _urls(requests) == ["https://host/operator/tokens/mint"]


def test_mint_token_none_when_unconfigured() -> None:
    """No bearer or reachable host hides the connection."""

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") is None


def test_mint_token_none_on_transport_error(settings: pytest.FixtureRequest) -> None:
    """A failed mint call hides the connection rather than raising."""

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise OSError("connection refused")

    with mock_transport(refuse):
        assert OperatorDaemon.from_settings().mint_token("auth/user:abc") is None


def test_not_sent_transport_failure_preserves_daemon_unavailability(settings: pytest.FixtureRequest) -> None:
    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise RequestNotSentError()

    daemon = OperatorDaemon.from_settings()
    with mock_transport(refuse), pytest.raises(OperatorDaemonError) as raised:
        assert daemon.mint_token("auth/user:abc") is None
        assert daemon.introspect_sdl() is None
        daemon.read_file("app", "settings.yaml")
    assert isinstance(raised.value.__cause__, RequestNotSentError)


def test_mint_token_none_on_daemon_http_error(settings: pytest.FixtureRequest) -> None:
    """A daemon HTTP failure hides the connection rather than bubbling through GraphQL."""

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"

    with mock_transport(_answer(500, b"no")):
        assert OperatorDaemon.from_settings().mint_token("auth/user:abc") is None


def test_mint_token_reuses_cached_actor_token(settings: pytest.FixtureRequest) -> None:
    """A browser connection query reuses the short-lived daemon token for the same actor window."""

    cache.clear()
    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    settings.ANGEE_OPERATOR_TOKEN_SCOPE = ["service:read"]
    settings.ANGEE_OPERATOR_TOKEN_TTL = "30m"
    minted: list[httpx2.Request] = []

    def mint(request: httpx2.Request) -> httpx2.Response:
        minted.append(request)
        return httpx2.Response(200, json={"token": f"minted-{len(minted)}", "expires_at": "2099-01-01T00:00:00Z"})

    daemon = OperatorDaemon.from_settings()
    expiry = datetime(2099, 1, 1, tzinfo=timezone.utc)
    with mock_transport(mint) as requests:
        assert daemon.mint_token("auth/user:abc") == ConnectionToken("minted-1", expiry)
        assert daemon.mint_token("auth/user:abc") == ConnectionToken("minted-1", expiry)
        assert daemon.mint_token("auth/user:def") == ConnectionToken("minted-2", expiry)
    assert [(str(request.url), json.loads(request.content)) for request in requests] == [
        (
            "http://localhost:9000/tokens/mint",
            {"actor": "auth/user:abc", "scope": ["service:read"], "ttl": "30m"},
        ),
        (
            "http://localhost:9000/tokens/mint",
            {"actor": "auth/user:def", "scope": ["service:read"], "ttl": "30m"},
        ),
    ]
    cache.clear()


# --- resolver gate ------------------------------------------------------------


def _execute() -> strawberry.types.ExecutionResult:
    """Run the connection query against a freshly built schema."""

    schema = strawberry.Schema(query=operator_schema.OperatorQuery)
    return schema.execute_sync(_CONNECTION_QUERY)


class _StubDaemon:
    """Stand-in daemon that returns a fixed token without any network call."""

    endpoint = "http://localhost:9000/graphql"
    restart_job = "provision"

    def __init__(self, token: str | None) -> None:
        self._token = token
        self.minted_for: str | None = None

    def mint_token(self, actor: str) -> ConnectionToken | None:
        self.minted_for = actor
        return ConnectionToken(self._token, datetime(2099, 1, 1, tzinfo=timezone.utc)) if self._token else None


def test_connection_hidden_for_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """No actor hides the connection without touching the gate."""

    monkeypatch.setattr(operator_schema, "current_actor", lambda: None)

    result = _execute()

    assert result.errors is None
    assert result.data == {"operatorConnection": None}


def test_connection_hidden_when_read_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """An actor denied ``read`` on the connection sees ``None``."""

    monkeypatch.setattr(operator_schema, "current_actor", lambda: _ACTOR)
    monkeypatch.setattr(operator_schema, "backend", lambda: object())
    monkeypatch.setattr(
        operator_schema,
        "check_field_access",
        lambda *args, **kwargs: SimpleNamespace(allowed=False),
    )

    result = _execute()

    assert result.errors is None
    assert result.data == {"operatorConnection": None}


def test_connection_hidden_when_mint_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """An authorized actor sees ``None`` when no token can be minted."""

    stub = _StubDaemon(token=None)
    monkeypatch.setattr(operator_schema, "current_actor", lambda: _ACTOR)
    monkeypatch.setattr(operator_schema, "backend", lambda: object())
    monkeypatch.setattr(
        operator_schema,
        "check_field_access",
        lambda *args, **kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(operator_schema.OperatorDaemon, "from_settings", classmethod(lambda cls: stub))

    result = _execute()

    assert result.errors is None
    assert result.data == {"operatorConnection": None}


def test_connection_returns_minted_token_for_authorized_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """An authorized actor receives the endpoint and a token minted for them."""

    stub = _StubDaemon(token="minted-xyz")
    monkeypatch.setattr(operator_schema, "current_actor", lambda: _ACTOR)
    monkeypatch.setattr(operator_schema, "backend", lambda: object())
    monkeypatch.setattr(
        operator_schema,
        "check_field_access",
        lambda *args, **kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(operator_schema.OperatorDaemon, "from_settings", classmethod(lambda cls: stub))

    result = _execute()

    assert result.errors is None
    assert result.data == {
        "operatorConnection": {
            "endpoint": "http://localhost:9000/graphql",
            "token": "minted-xyz",
            "restartJob": "provision",
        }
    }
    assert stub.minted_for == "auth/user:abc"


# --- schema surface -----------------------------------------------------------


def test_operator_contributes_only_the_console_surface() -> None:
    """The addon installs its query and type into the console bucket only."""

    assert set(operator_schema.schemas) == {"console"}
    console = operator_schema.schemas["console"]
    assert operator_schema.OperatorQuery in console["query"]
    assert operator_schema.OperatorConnectionInfo in console["types"]


# --- REBAC const-canon reach (F-g) --------------------------------------------



# `operator/connection` / `operator/role` reference these cross-package types; the
# probe evaluates the *real* permissions.zed against a standalone schema, so the
# referenced externals are defined minimally here.
_OPERATOR_SCHEMA_PREAMBLE = """
definition auth/user {}
definition auth/group { relation member: auth/user }
definition angee/role { relation member: auth/user | auth/group#member }
"""
_OPERATOR_ZED = Path(__file__).resolve().parents[1] / "src/angee/operator/permissions.zed"


@pytest.mark.django_db
def test_operator_admin_role_reaches_connection_read_tuple_free() -> None:
    """`operator/connection#read` resolves for an operator_admin member tuple-free.

    The migrated def reaches the role through the const canon
    (`reader->effective_member`), mirroring storage/backend. The role membership is
    the only tuple written — no per-object `reader` tuple — and a non-member is
    denied cleanly (never a SchemaError from the walk into `operator/role#admin`).
    """

    schema = parse_zed(_OPERATOR_SCHEMA_PREAMBLE + _OPERATOR_ZED.read_text(encoding="utf-8"))

    connection = schema.get_definition("operator/connection")
    assert connection is not None
    reader = next(relation for relation in connection.relations if relation.name == "reader")
    assert reader.backing == ConstBinding(target_id="operator_admin")

    backend = install_schema(schema)
    operator = SubjectRef.of("auth/user", "operator-1")
    connection_ref = ObjectRef("operator/connection", "default")

    # A non-member is denied — and the walk into `operator/role#admin` resolves to
    # a clean deny, not a SchemaError.
    assert not backend.has_access(subject=operator, action="read", resource=connection_ref)

    backend.write_relationships(
        [
            RelationshipTuple(
                resource=ObjectRef("operator/role", "operator_admin"),
                relation="member",
                subject=operator,
            )
        ]
    )

    # The single role-membership row opens `read` through `reader->effective_member`.
    assert backend.has_access(subject=operator, action="read", resource=connection_ref)


def test_operator_schema_writes_the_live_sdl_into_the_runtime(monkeypatch, settings, tmp_path) -> None:
    """The stack job's export lands in the runtime, never in the operator package's committed snapshot."""

    settings.COMPOSE_OUTPUT_DIR = tmp_path
    stub = SimpleNamespace(
        admin_bearer="token", server_base="http://operator", introspect_sdl=lambda: "type Query { ping: String }",
    )
    monkeypatch.setattr(operator_schema_command.OperatorDaemon, "from_settings", classmethod(lambda cls: stub))
    committed = Path(operator_schema_command.__file__).resolve().parents[2] / "web" / "schema" / "operator.graphql"
    before = committed.read_bytes()

    call_command("operator_schema", retries=1)

    assert (tmp_path / "schemas" / "external" / "operator.graphql").read_text() == "type Query { ping: String }\n"
    assert committed.read_bytes() == before


def test_operator_schema_rewrites_the_runtime_sdl_only_when_it_changed(monkeypatch, settings, tmp_path) -> None:
    """An unchanged SDL keeps the file frontend codegen watches; a changed SDL republishes it."""

    settings.COMPOSE_OUTPUT_DIR = tmp_path
    stub = SimpleNamespace(
        admin_bearer="token", server_base="http://operator", introspect_sdl=lambda: "type Query { ping: String }",
    )
    monkeypatch.setattr(operator_schema_command.OperatorDaemon, "from_settings", classmethod(lambda cls: stub))
    target = tmp_path / "schemas" / "external" / "operator.graphql"

    def export() -> str:
        output = StringIO()
        call_command("operator_schema", retries=1, stdout=output)
        return output.getvalue()

    assert "unchanged" not in export()
    written = target.stat()

    assert "operator schema unchanged" in export()
    assert (target.stat().st_ino, target.stat().st_mtime_ns) == (written.st_ino, written.st_mtime_ns)

    stub.introspect_sdl = lambda: "type Query { pong: String }"
    assert "unchanged" not in export()
    assert target.stat().st_ino != written.st_ino
    assert target.read_text() == "type Query { pong: String }\n"


def test_connection_cache_lifetime_uses_issued_expiry_instead_of_requested_ttl(settings, monkeypatch):
    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin"
    settings.ANGEE_OPERATOR_TOKEN_TTL = "1h"
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    monkeypatch.setattr(daemon_module.timezone, "now", lambda: now)
    capped = {"token": "capped", "expires_at": (now + timedelta(seconds=60)).isoformat()}
    stored = []
    monkeypatch.setattr(cache, "set", lambda key, value, timeout: stored.append((value, timeout)))
    with mock_transport(lambda request: httpx2.Response(200, json=capped)):
        issued = OperatorDaemon.from_settings().mint_token("auth/user:capped")
    assert issued == ConnectionToken("capped", now + timedelta(seconds=60))
    assert stored == [(issued, 55)]


@pytest.mark.parametrize("expiry", [None, "bad", "2000-01-01T00:00:00Z", "2099-01-01T00:00:00"])
def test_mint_refuses_absent_invalid_naive_or_expired_issue_metadata(settings, expiry):
    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin"
    issued = {"token": "refused", "expires_at": expiry}
    with mock_transport(lambda request: httpx2.Response(200, json=issued)):
        assert OperatorDaemon.from_settings().mint_token("auth/user:invalid-expiry") is None


def test_operator_has_no_catalogue_execution_or_checkout_api():
    assert not hasattr(OperatorDaemon, "source_checkout")
    assert not hasattr(OperatorDaemon, "run_source_build")
