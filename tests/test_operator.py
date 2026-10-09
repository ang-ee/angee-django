"""Tests for the operator daemon-bridge addon."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator, cast

import httpx2
import pytest
import strawberry
from django.core.cache import cache
from django.core.management import call_command
from rebac import ObjectRef, RelationshipTuple, SubjectRef
from rebac.schema import ConstBinding, parse_zed

from angee.operator import daemon as daemon_module
from angee.operator import schema as operator_schema
from angee.operator.daemon import (
    OperatorDaemon,
    OperatorDaemonConflict,
    OperatorDaemonError,
    OperatorDaemonNotFound,
    OperatorInstanceKind,
    WorkspaceStatus,
)
from angee.operator.management.commands import operator_schema as operator_schema_command
from angee.testing.permissions import install_permission_schema

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


def _daemon_answering(monkeypatch: pytest.MonkeyPatch, status: int, body: bytes) -> tuple[OperatorDaemon, list[str]]:
    """Return a daemon whose every REST call gets ``status``/``body``, and the URLs it called."""

    urls: list[str] = []

    class FakeHttpClient:
        def request(self, method: str, url: str, **kwargs: object) -> httpx2.Response:
            del method, kwargs
            urls.append(url)
            return httpx2.Response(status, content=body)

    monkeypatch.setattr(daemon_module, "HttpClient", FakeHttpClient)
    daemon = OperatorDaemon(
        endpoint="http://op/graphql",
        server_base="http://op",
        admin_bearer="admin",
        scope=(),
        ttl="1h",
    )
    return daemon, urls


def test_daemon_request_raises_typed_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A create the daemon refuses with 409 raises the typed conflict from the REST call."""

    daemon, _ = _daemon_answering(
        monkeypatch, 409, b'{"kind": "service", "name": "agent-demo", "error": "service agent-demo conflicts"}'
    )

    with pytest.raises(OperatorDaemonConflict) as raised:
        daemon.create_service(template="services/agent", workspace="demo", inputs={})

    assert (raised.value.kind, raised.value.name) == (OperatorInstanceKind.SERVICE, "agent-demo")


def test_workspace_status_reads_template_inputs_and_mounting_services(monkeypatch: pytest.MonkeyPatch) -> None:
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
    daemon, urls = _daemon_answering(monkeypatch, 200, body)

    assert daemon.workspace_status("demo ws") == WorkspaceStatus(
        name="demo ws",
        template="workspaces/agent-default",
        inputs={"agent_name": "Demo", "instructions": "Hi."},
        services=("agent-demo",),
    )
    assert urls == ["http://op/workspaces/demo%20ws/status"]


def test_service_status_reads_the_service_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon has no single-service read; the status comes from its service listing."""

    body = b'{"nodes": [{"name": "agent-demo", "runtime": "container", "status": "exited"}], "total_count": 1}'
    daemon, urls = _daemon_answering(monkeypatch, 200, body)

    assert daemon.service_status("agent-demo") == "exited"
    assert daemon.service_status("agent-other") is None
    assert urls == ["http://op/services", "http://op/services"]


@pytest.mark.parametrize(
    ("call", "body"),
    [
        (lambda daemon: daemon.destroy_service("svc"), b'{"kind": "service", "name": "svc", "error": "gone"}'),
        (lambda daemon: daemon.destroy_workspace("ws"), b'{"kind": "workspace", "name": "ws", "error": "gone"}'),
        (lambda daemon: daemon.workspace_status("ws"), b'{"kind": "workspace", "name": "ws", "error": "gone"}'),
    ],
)
def test_an_instance_is_absent_only_when_the_not_found_names_it(
    monkeypatch: pytest.MonkeyPatch, call: Callable[[OperatorDaemon], object], body: bytes
) -> None:
    """A not-found naming the asked-for instance means it is gone: destroyed already, or no status."""

    daemon, _ = _daemon_answering(monkeypatch, 404, body)

    assert call(daemon) is None


@pytest.mark.parametrize(
    "body",
    [
        b'{"error": "service \\"svc\\" is not declared"}',
        b"404 page not found",
        b'{"kind": "service", "name": "other-svc", "error": "gone"}',
        b'{"kind": "workspace", "name": "svc", "error": "gone"}',
    ],
)
def test_a_not_found_that_names_another_instance_still_raises(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    """A plain 404 — a proxy, a mis-mounted URL — or one about another instance is never read as destroyed."""

    daemon, _ = _daemon_answering(monkeypatch, 404, body)

    with pytest.raises(OperatorDaemonNotFound) as raised:
        daemon.destroy_service("svc")

    assert raised.value.status_code == 404
    assert str(raised.value).startswith("operator POST destroy: HTTP 404")


def test_daemon_request_uses_the_shared_integrate_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator REST calls ride the shared pinned HTTP owner, allowing local daemon addresses."""

    calls: list[dict[str, object]] = []

    class FakeHttpClient:
        def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            body: bytes | None = None,
            allow_private: bool = False,
            timeout: int = 60,
        ) -> httpx2.Response:
            calls.append(
                {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "body": body,
                    "allow_private": allow_private,
                    "timeout": timeout,
                }
            )
            return httpx2.Response(200, content=b'{"ok": true}')

    monkeypatch.setattr(daemon_module, "HttpClient", FakeHttpClient)
    daemon = OperatorDaemon(
        endpoint="http://op/graphql",
        server_base="http://op",
        admin_bearer="admin",
        scope=(),
        ttl="1h",
    )

    assert daemon._request("POST", "http://op/workspaces", {"name": "demo"}, timeout=7) == {"ok": True}
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://op/workspaces"
    assert call["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer admin",
    }
    assert json.loads(cast(bytes, call["body"]).decode()) == {"name": "demo"}
    assert call["allow_private"] is True
    assert call["timeout"] == 7


def test_resolve_template_ref_reads_collection_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon's template REST list is a collection envelope, not a bare array."""

    daemon = OperatorDaemon(
        endpoint="http://op/graphql",
        server_base="http://op",
        admin_bearer="admin",
        scope=(),
        ttl="1h",
    )

    def fake_request(
        self: OperatorDaemon,
        method: str,
        url: str,
        payload: dict[str, object] | None = None,
        *,
        timeout: int = 60,
    ) -> dict[str, object]:
        del self, payload, timeout
        assert method == "GET"
        assert url == "http://op/templates"
        return {
            "nodes": [
                {"name": "agent-default", "kind": "service", "ref": "services/wrong-kind"},
                {"name": "agent-default", "kind": "workspace", "ref": "workspaces/agent-default"},
            ],
            "total_count": 2,
        }

    monkeypatch.setattr(OperatorDaemon, "_request", fake_request)

    assert daemon.resolve_template_ref(name="agent-default", kind="workspace") == "workspaces/agent-default"


def test_file_tools_call_the_files_api_and_carry_the_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_file/write_file hit ``/files?source=&path=`` carrying the etag."""

    daemon = OperatorDaemon(
        endpoint="http://op/graphql",
        server_base="http://op",
        admin_bearer="admin",
        scope=(),
        ttl="1h",
    )
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(
        self: OperatorDaemon,
        method: str,
        url: str,
        payload: dict[str, object] | None = None,
        *,
        timeout: int = 60,
    ) -> dict[str, object]:
        del self, timeout
        calls.append((method, url, payload))
        if method == "GET":
            return {"source": "app", "path": "settings.yaml", "content": "INSTALLED_APPS: []\n", "etag": "e1"}
        if method == "PUT":
            return {"source": "app", "path": "settings.yaml", "etag": "e2"}
        raise AssertionError(f"Unexpected method: {method}")

    monkeypatch.setattr(OperatorDaemon, "_request", fake_request)

    remote = daemon.read_file("app", "settings.yaml")
    assert (remote.content, remote.etag) == ("INSTALLED_APPS: []\n", "e1")
    assert daemon.write_file("app", "settings.yaml", "INSTALLED_APPS: [x]\n", "e1") == "e2"

    get_method, get_url, _ = calls[0]
    assert get_method == "GET"
    assert get_url.startswith("http://op/files?") and "source=app" in get_url and "path=settings.yaml" in get_url
    put_method, put_url, put_payload = calls[1]
    assert put_method == "PUT" and put_url.startswith("http://op/files?")
    assert put_payload == {"content": "INSTALLED_APPS: [x]\n", "etag": "e1"}
    assert len(calls) == 2


# --- endpoint resolution ------------------------------------------------------
#
# The daemon resolves from Django settings only. Dev stack env and project YAML
# are normalized by ``angee.compose.settings`` before apps read configuration.


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


def test_mint_token_posts_actor_scope_ttl_and_returns_token(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured bridge mints over the admin bearer and returns the token."""

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    settings.ANGEE_OPERATOR_TOKEN_SCOPE = ["service:read"]
    settings.ANGEE_OPERATOR_TOKEN_TTL = "30m"
    seen: dict[str, object] = {}

    def fake_post(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        seen.update(url=url, payload=payload, bearer=self.admin_bearer)
        return {"token": "minted-abc"}

    monkeypatch.setattr(OperatorDaemon, "_post_json", fake_post)

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == "minted-abc"
    assert seen["url"] == "http://localhost:9000/tokens/mint"
    assert seen["payload"] == {"actor": "auth/user:abc", "scope": ["service:read"], "ttl": "30m"}
    assert seen["bearer"] == "admin-bearer"


def test_mint_token_derives_host_from_full_graphql_endpoint(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mint host is derived from a full GraphQL endpoint when that is all that is set."""

    settings.ANGEE_OPERATOR_GRAPHQL_ENDPOINT = "http://daemon:9000/graphql"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    seen: dict[str, object] = {}

    def fake_post(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        seen["url"] = url
        return {"token": "ok"}

    monkeypatch.setattr(OperatorDaemon, "_post_json", fake_post)

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == "ok"
    assert seen["url"] == "http://daemon:9000/tokens/mint"


def test_mint_token_preserves_mount_prefix(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon behind a path prefix mints under that prefix, not the bare host.

    Otherwise the admin bearer would POST to a sibling service on the same origin
    (``https://host/tokens/mint`` instead of ``https://host/operator/...``).
    """

    settings.ANGEE_OPERATOR_URL = "https://host/operator"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    seen: dict[str, object] = {}

    def fake_post(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        seen["url"] = url
        return {"token": "ok"}

    monkeypatch.setattr(OperatorDaemon, "_post_json", fake_post)

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == "ok"
    assert seen["url"] == "https://host/operator/tokens/mint"


def test_mint_token_strips_only_trailing_graphql_from_prefixed_endpoint(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prefixed GraphQL endpoint keeps the prefix, dropping only ``/graphql``."""

    settings.ANGEE_OPERATOR_GRAPHQL_ENDPOINT = "https://host/operator/graphql"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    seen: dict[str, object] = {}

    def fake_post(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        seen["url"] = url
        return {"token": "ok"}

    monkeypatch.setattr(OperatorDaemon, "_post_json", fake_post)

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") == "ok"
    assert seen["url"] == "https://host/operator/tokens/mint"


def test_mint_token_none_when_unconfigured() -> None:
    """No bearer or reachable host hides the connection."""

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") is None


def test_mint_token_none_on_transport_error(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed mint call hides the connection rather than raising."""

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"

    def boom(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        raise OSError("connection refused")

    monkeypatch.setattr(OperatorDaemon, "_post_json", boom)

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") is None


def test_mint_token_none_on_daemon_http_error(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon HTTP failure hides the connection rather than bubbling through GraphQL."""

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"

    def boom(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        del self, url, payload
        raise OperatorDaemonError("operator POST mint: HTTP 500: no")

    monkeypatch.setattr(OperatorDaemon, "_post_json", boom)

    assert OperatorDaemon.from_settings().mint_token("auth/user:abc") is None


def test_mint_token_reuses_cached_actor_token(
    settings: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A browser connection query reuses the short-lived daemon token for the same actor window."""

    cache.clear()
    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"
    settings.ANGEE_OPERATOR_TOKEN = "admin-bearer"
    settings.ANGEE_OPERATOR_TOKEN_SCOPE = ["service:read"]
    settings.ANGEE_OPERATOR_TOKEN_TTL = "30m"
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post(self: OperatorDaemon, url: str, payload: dict[str, object]) -> dict[str, object]:
        del self
        calls.append((url, payload))
        return {"token": f"minted-{len(calls)}"}

    monkeypatch.setattr(OperatorDaemon, "_post_json", fake_post)
    daemon = OperatorDaemon.from_settings()

    assert daemon.mint_token("auth/user:abc") == "minted-1"
    assert daemon.mint_token("auth/user:abc") == "minted-1"
    assert daemon.mint_token("auth/user:def") == "minted-2"
    assert calls == [
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

    def mint_token(self, actor: str) -> str | None:
        self.minted_for = actor
        return self._token


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
_OPERATOR_ZED = Path(__file__).resolve().parents[1] / "addons/angee/operator/permissions.zed"


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

    backend = install_permission_schema(schema)
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

    settings.ANGEE_RUNTIME_DIR = tmp_path
    stub = SimpleNamespace(
        admin_bearer="token", server_base="http://operator", introspect_sdl=lambda: "type Query { ping: String }",
    )
    monkeypatch.setattr(operator_schema_command.OperatorDaemon, "from_settings", classmethod(lambda cls: stub))
    committed = Path(operator_schema_command.__file__).resolve().parents[2] / "web" / "schema" / "operator.graphql"
    before = committed.read_bytes()

    call_command("operator_schema", retries=1)

    assert (tmp_path / "schemas" / "external" / "operator.graphql").read_text() == "type Query { ping: String }\n"
    assert committed.read_bytes() == before
