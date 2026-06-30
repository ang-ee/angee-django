"""Tests for the operator daemon-bridge addon."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
import strawberry
from rebac import SubjectRef

from angee.operator import schema as operator_schema
from angee.operator.daemon import OperatorDaemon, OperatorDaemonNotFound, _daemon_error

_CONNECTION_QUERY = "{ operatorConnection { endpoint token } }"
_ACTOR = SubjectRef.of("auth/user", "abc")


def test_daemon_error_surfaces_the_response_body() -> None:
    """A daemon HTTP error reports its body (JSON ``error`` field, else text), not a bare status."""

    def err(code: int, body: bytes) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("http://op/x", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]

    assert _daemon_error(err(500, b'{"error": "secret \\"x\\" is not resolved"}')) == (
        'HTTP 500: secret "x" is not resolved'
    )
    assert _daemon_error(err(409, b'{"reason": "already exists"}')) == "HTTP 409: already exists"
    assert _daemon_error(err(503, b"upstream down")) == "HTTP 503: upstream down"


def test_daemon_request_raises_typed_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 404 remains a readable daemon error and is typed for idempotent teardown."""

    def missing(_request: urllib.request.Request, timeout: int) -> None:
        del timeout
        raise urllib.error.HTTPError(
            "http://op/services/svc/destroy",
            404,
            "not found",
            {},
            io.BytesIO(b'{"error": "service \\"svc\\" is not declared"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", missing)
    daemon = OperatorDaemon(
        endpoint="http://op/graphql",
        server_base="http://op",
        admin_bearer="admin",
        scope=(),
        ttl="1h",
    )

    with pytest.raises(OperatorDaemonNotFound) as raised:
        daemon.destroy_service("svc")

    assert raised.value.status_code == 404
    assert str(raised.value) == 'operator POST destroy: HTTP 404: service "svc" is not declared'


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
    """read_file/write_file hit ``/files?source=&path=`` carrying the etag; stack_build hits ``/stack/build``."""

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
        return {"status": "queued"}

    monkeypatch.setattr(OperatorDaemon, "_request", fake_request)

    remote = daemon.read_file("app", "settings.yaml")
    assert (remote.content, remote.etag) == ("INSTALLED_APPS: []\n", "e1")
    assert daemon.write_file("app", "settings.yaml", "INSTALLED_APPS: [x]\n", "e1") == "e2"
    assert daemon.stack_build() == "queued"

    get_method, get_url, _ = calls[0]
    assert get_method == "GET"
    assert get_url.startswith("http://op/files?") and "source=app" in get_url and "path=settings.yaml" in get_url
    put_method, put_url, put_payload = calls[1]
    assert put_method == "PUT" and put_url.startswith("http://op/files?")
    assert put_payload == {"content": "INSTALLED_APPS: [x]\n", "etag": "e1"}
    assert calls[2] == ("POST", "http://op/stack/build", {})


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

    settings.ANGEE_OPERATOR_URL = "http://localhost:9000"

    assert OperatorDaemon.from_settings().endpoint == "http://localhost:9000/graphql"


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


# --- resolver gate ------------------------------------------------------------


def _execute() -> strawberry.types.ExecutionResult:
    """Run the connection query against a freshly built schema."""

    schema = strawberry.Schema(query=operator_schema.OperatorQuery)
    return schema.execute_sync(_CONNECTION_QUERY)


class _StubDaemon:
    """Stand-in daemon that returns a fixed token without any network call."""

    endpoint = "http://localhost:9000/graphql"

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
