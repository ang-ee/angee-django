"""Tests for the uvicorn-backed ``runserver`` override."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from django.apps import AppConfig, apps
from django.core.management import get_commands
from django.core.management.commands.runserver import Command as DjangoRunserver

from angee.server.management.commands.runserver import Command


def test_runserver_is_a_django_runserver_subclass() -> None:
    """The override subclasses Django's runserver so it inherits the reloader."""

    assert issubclass(Command, DjangoRunserver)


def test_native_command_precedes_staticfiles_when_foundation_is_first(settings: Any) -> None:
    """Django discovers the host override even with the competing contrib command."""

    host = AppConfig.create("angee.server")
    settings.INSTALLED_APPS = [
        host, "django.contrib.staticfiles", *(config for config in apps.get_app_configs() if config is not host),
    ]
    assert get_commands()["runserver"] == "angee.server"
    assert get_commands()["serve"] == "angee.server"


def test_inner_run_serves_resolved_app(monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
    """``inner_run`` resolves the configured app and runs uvicorn."""

    settings.ASGI_APPLICATION = "angee.server.application"
    app = object()
    captured: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, config: Any) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    module = "angee.server.management.commands.runserver"
    monkeypatch.setattr(f"{module}.import_string", lambda path: app)
    monkeypatch.setattr("uvicorn.Server", _FakeServer)

    command = Command()
    command.addr = "127.0.0.1"
    command.port = "8123"
    # Stub the boot-time Django checks (they need the DB/full registry); we are
    # exercising the uvicorn wiring, not the checks.
    monkeypatch.setattr(command, "check", lambda **kwargs: None)
    monkeypatch.setattr(command, "check_migrations", lambda: None)
    command.inner_run()

    config = captured["config"]
    assert config.app is app
    assert config.host == "127.0.0.1"
    assert config.port == 8123
    assert config.reload is False
    assert captured["ran"] is True


def test_native_reload_closes_lifespan_work_after_unhandled_change() -> None:
    """Native handled-file semantics survive, and reload closes ASGI-owned threads."""

    script = r'''
import json
import os
from pathlib import Path
from threading import Event, Thread

import django
import uvicorn
from django.conf import settings
from django.utils import autoreload

from angee.server.management.commands import runserver

settings.configure(DEBUG=True, INSTALLED_APPS=[], ASGI_APPLICATION="__main__.application")
django.setup()
startup = Event()
stop_work = Event()
lifecycle = []
workers = []

async def application(scope, receive, send):
    assert scope["type"] == "lifespan"
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            worker = Thread(target=stop_work.wait, daemon=False)
            workers.append(worker)
            worker.start()
            lifecycle.append("startup")
            startup.set()
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            stop_work.set()
            for worker in workers:
                worker.join()
            lifecycle.append("shutdown")
            await send({"type": "lifespan.shutdown.complete"})
            return

class SocketFreeServer(uvicorn.Server):
    async def startup(self, sockets=None):
        # Exercise native serve/main_loop/shutdown and lifespan without a listener.
        await self.lifespan.startup()
        self.servers = []
        self.started = True

class NativeChanges(autoreload.StatReloader):
    def tick(self):
        assert startup.wait(10), "ASGI lifespan did not start"
        self.notify_file_changed(Path("handled.py"))
        assert lifecycle == ["startup"]
        assert workers[0].is_alive()
        self.notify_file_changed(Path("reload.py"))
        raise AssertionError("Django did not raise its reload exit")
        yield

def handle_change(sender, file_path, **kwargs):
    return file_path.name == "handled.py"

autoreload.file_changed.connect(handle_change, weak=False)
autoreload.get_reloader = NativeChanges
os.environ[autoreload.DJANGO_AUTORELOAD_ENV] = "true"
original_trigger = autoreload.trigger_reload
uvicorn.Server = SocketFreeServer
command = runserver.Command()
command.addr = "127.0.0.1"
command.port = "0"
command.check_migrations = lambda: None
try:
    command.run(use_reloader=True, skip_checks=True)
except SystemExit as error:
    assert error.code == 3
else:
    raise AssertionError("Missing reload exit")
assert autoreload.trigger_reload is original_trigger
assert lifecycle == ["startup", "shutdown"]
assert workers and not any(worker.is_alive() for worker in workers)
print(json.dumps({"lifecycle": lifecycle, "reload_code": 3}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "lifecycle": ["startup", "shutdown"], "reload_code": 3,
    }
