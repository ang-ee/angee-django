"""Native job transport remains the authority for package-change operations."""
from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.operator import daemon as daemon_module
from angee.operator.daemon import OperatorDaemon
from angee.platform.installer import LocalInstallerBackend
from angee.platform_integrate_operator.installer import OperatorInstallerBackend


def test_operator_dependency_change_returns_the_native_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    record = {"id": "native-operation", "root_job": "py-sync", "status": "pending"}

    class Client:
        def request(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
            calls.append((method, url, json.loads(kwargs["body"])))
            assert kwargs["headers"]["Authorization"] == "Bearer private"
            assert kwargs["allow_private"] is True
            return httpx2.Response(202, json=record)

    daemon = OperatorDaemon("http://op/graphql", "http://op/prefix", "private", (), "1h")
    monkeypatch.setattr(daemon_module, "HttpClient", Client)
    monkeypatch.setattr(OperatorDaemon, "from_settings", classmethod(lambda cls: daemon))
    backend = OperatorInstallerBackend()
    assert backend.queue_dependency_change("example-addon>=2") == record
    assert backend.queue_dependency_change("example-addon", remove=True) == record
    assert calls == [
        ("POST", "http://op/prefix/jobs/py-sync/run", {
            "inputs": {"action": "add", "requirement": "example-addon>=2"}, "chained_restart": True,
        }),
        ("POST", "http://op/prefix/jobs/py-sync/run", {
            "inputs": {"action": "remove", "requirement": "example-addon"}, "chained_restart": True,
        }),
    ]


def test_local_settings_transport_cannot_mutate_a_running_environment() -> None:
    with pytest.raises(ImproperlyConfigured, match="operator installer backend"):
        LocalInstallerBackend().queue_dependency_change("example-addon")
