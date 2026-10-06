"""Sentry starts only for a configured DSN and never receives sanitized GraphQL data."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sentry_sdk
import strawberry
from django.test import Client, override_settings
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from strawberry.django.views import GraphQLView

from angee.compose.project import ProjectContract
from angee.graphql.schema import GraphQLSchemas
from tests.test_graphql import addon
from tests.test_settings_composition_errors import _environment, _project

DSN = "https://publickey@sentry.example.invalid/1"
_RESOLVER_SECRET = "resolver-value-canary"


@strawberry.input
class _Credentials:
    password: str


@strawberry.type
class _Query:
    @strawberry.field
    def failure(self, credentials: _Credentials) -> str:
        raise RuntimeError(_RESOLVER_SECRET)

    @strawberry.field
    def echo(self, value: int) -> int:
        return value


_PROBE = """
import json
import sentry_sdk
from angee.compose.project import ProjectContract

try:
    ProjectContract({"__name__": "probe_settings"}).compose()
    failure = None
except Exception as error:
    failure = type(error).__name__
client = sentry_sdk.get_client()
print(json.dumps({
    "failure": failure,
    "active": client.is_active(),
    "dsn": client.dsn,
    "environment": client.options.get("environment"),
    "send_default_pii": client.options.get("send_default_pii"),
    "include_local_variables": client.options.get("include_local_variables"),
    "max_request_body_size": client.options.get("max_request_body_size"),
    "integrations": sorted(client.integrations),
}))
"""


class _RecordingTransport(Transport):
    """Keep every event Sentry would send instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events.extend(event for item in envelope.items if (event := item.get_event()) is not None)


def _compose(tmp_path: Path, **environment: str) -> dict[str, Any]:
    """Compose the project in tmp_path in a fresh interpreter and report Sentry's client."""

    if not (tmp_path / "settings.yaml").exists():
        (tmp_path / "settings.yaml").write_text(
            'SECRET_KEY: isolated-error-reporting-test\nINSTALLED_APPS: []\nANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n',
            encoding="utf-8",
        )
    env = _environment(tmp_path)
    for name in ("SENTRY_DSN", "SENTRY_ENVIRONMENT", "SENTRY_RELEASE"):
        env.pop(name, None)
    env.update(environment)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_without_a_dsn_sentry_stays_uninitialised(tmp_path: Path) -> None:
    assert _compose(tmp_path)["active"] is False


@pytest.mark.parametrize("environment", ["development", "production"])
def test_a_dsn_starts_sentry_with_privacy_preserving_options(tmp_path: Path, environment: str) -> None:
    client = _compose(tmp_path, SENTRY_DSN=DSN, SENTRY_ENVIRONMENT=environment)

    assert {key: value for key, value in client.items() if key != "integrations"} == {
        "failure": None,
        "active": True,
        "dsn": DSN,
        "environment": environment,
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
    }
    assert {"django", "celery", "logging"} <= set(client["integrations"])
    assert "strawberry" not in client["integrations"]


def test_a_composition_failure_is_reported(tmp_path: Path) -> None:
    _project(tmp_path)
    client = _compose(tmp_path, SENTRY_DSN=DSN)

    assert client["failure"] == "ImproperlyConfigured"
    assert client["active"] is True


@pytest.mark.django_db
def test_graphql_failures_reach_sentry_once_without_sanitized_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay ProjectContract's own Sentry options against the GraphQL view."""

    start = sentry_sdk.init
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **options: recorded.append(options))
    monkeypatch.setenv("SENTRY_DSN", DSN)
    ProjectContract({})._start_error_reporting()
    [options] = recorded

    schema = GraphQLSchemas([addon(public={"query": [_Query]})]).build("public")
    urls = ModuleType("error_reporting_urls")
    urls.urlpatterns = [path("graphql/", csrf_exempt(GraphQLView.as_view(schema=schema)))]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, urls.__name__, urls)
    transport = _RecordingTransport()

    def requests() -> None:
        client = Client()
        for query, variables in (
            ("query Q($c: Credentials!) { failure(credentials: $c) }", {"c": {"password": "password-canary"}}),
            ("query Q($value: Int!) { echo(value: $value) }", {"value": "coercion-canary"}),
        ):
            client.post(
                "/graphql/",
                data=json.dumps({"query": query, "variables": variables}),
                content_type="application/json",
            )

    start(dsn=DSN, transport=transport, **options)
    try:
        with override_settings(ROOT_URLCONF=urls.__name__, ALLOWED_HOSTS=["*"]):
            requests()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)

    events = transport.events
    serialized = json.dumps(events, default=str)
    assert len(events) == 1, serialized
    assert events[0]["logger"] == "angee.graphql.schema"
    for canary in (_RESOLVER_SECRET, "password-canary", "coercion-canary"):
        assert canary not in serialized
