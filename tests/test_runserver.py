"""Tests for the uvicorn-backed ``runserver`` override."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from django.core.management.commands.runserver import Command as DjangoRunserver

from angee.compose.management.commands import runserver as runserver_module
from angee.compose.management.commands.runserver import Command


def test_runserver_is_a_django_runserver_subclass() -> None:
    """The override subclasses Django's runserver so it inherits the reloader."""

    assert issubclass(Command, DjangoRunserver)


def test_inner_run_sets_sdl_gate_and_serves_resolved_app(monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
    """``inner_run`` sets the dev SDL gate, resolves the app, and runs uvicorn."""

    settings.ASGI_APPLICATION = "angee.asgi.application"
    monkeypatch.setenv("ANGEE_DEV_SDL", "")  # recorded as cleanup baseline
    app = object()
    captured: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, config: Any) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    module = "angee.compose.management.commands.runserver"
    monkeypatch.setattr(f"{module}.autoreload.trigger_reload", lambda filename: None)
    monkeypatch.setattr(f"{module}.import_string", lambda path: app)
    monkeypatch.setattr(f"{module}.uvicorn.Server", _FakeServer)

    command = Command()
    command.addr = "127.0.0.1"
    command.port = "8123"
    # Stub the boot-time Django checks (they need the DB/full registry); we are
    # exercising the uvicorn wiring, not the checks.
    monkeypatch.setattr(command, "check", lambda **kwargs: None)
    monkeypatch.setattr(command, "check_migrations", lambda: None)
    command.inner_run()

    assert os.environ["ANGEE_DEV_SDL"] == "1"
    assert runserver_module.autoreload.trigger_reload is runserver_module._force_process_reload
    config = captured["config"]
    assert config.app is app
    assert config.host == "127.0.0.1"
    assert config.port == 8123
    assert config.reload is False
    assert captured["ran"] is True


def test_force_process_reload_exits_with_django_reload_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reload exits the child process instead of waiting on runtime threads."""

    captured: dict[str, int] = {}

    class _Exited(Exception):
        pass

    def fake_exit(code: int) -> None:
        captured["code"] = code
        raise _Exited

    monkeypatch.setattr(runserver_module.os, "_exit", fake_exit)

    with pytest.raises(_Exited):
        runserver_module._force_process_reload(Path("addons/angee/agents/schema.py"))

    assert captured["code"] == 3
