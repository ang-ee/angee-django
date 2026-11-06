"""Contexts consume native parsed options without replacing Django dispatch."""

import os
import subprocess
import sys
from argparse import Action
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from django.core.management.base import BaseCommand, CommandError, connections

from angee.server import management


def _project(root: Path) -> None:
    package = root / "plain"
    commands = package / "management" / "commands"
    commands.mkdir(parents=True)
    for directory in (package, package / "management", commands):
        (directory / "__init__.py").write_text("")
    (package / "models.py").write_text("from django.db import models\nclass Record(models.Model): pass\n")
    (package / "contexts.py").write_text('''from contextlib import contextmanager
from django.apps import apps
@contextmanager
def first(command, argv, options):
    assert apps.get_model("plain.Record")
    assert argv[1] == command
    print("first-enter", command)
    try: yield
    finally: print("first-exit")
@contextmanager
def second(command, argv, options):
    print("second-enter")
    try: yield
    finally: print("second-exit")
''')
    (commands / "probe.py").write_text('''from django.core.management.base import BaseCommand
class Command(BaseCommand):
    requires_system_checks = []
    def handle(self, *args, **options): print("dispatched")
''')
    (root / "alternate.py").write_text('''SECRET_KEY = "test"
INSTALLED_APPS = ["plain"]
ANGEE_SERVER_COMMAND_CONTEXT = ["plain.contexts.first", "plain.contexts.second"]
''')
    (root / "manage.py").write_text(
        "from angee.server.management import execute\n"
        "if __name__ == '__main__': execute()\n"
    )


def _invoke(root: Path, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DJANGO_COMPOSE_MODE", None)
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, str(root / "manage.py"), *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_server_launcher_orders_contexts_after_native_setup(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _invoke(tmp_path, "probe", "--settings=alternate", f"--pythonpath={tmp_path}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "first-enter probe", "second-enter", "dispatched", "second-exit", "first-exit"
    ]


def test_server_launcher_help_skips_contexts(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _invoke(tmp_path, "probe", "--settings=alternate", f"--pythonpath={tmp_path}", "--help")
    assert result.returncode == 0, result.stderr
    assert "first-enter" not in result.stdout


def test_server_launcher_failed_runserver_setup_skips_context(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "plain" / "apps.py").write_text('''from django.apps import AppConfig
class PlainConfig(AppConfig):
    name = "plain"
    def ready(self): raise ValueError("setup failed")
''')
    (tmp_path / "alternate.py").write_text('''SECRET_KEY = "test"
INSTALLED_APPS = ["plain.apps.PlainConfig"]
ANGEE_SERVER_COMMAND_CONTEXT = ["plain.contexts.first"]
''')
    script = '''from django.core.management.commands.runserver import Command
Command.handle = lambda *args, **options: print("recoverable-dispatch")
from angee.server.management import execute
import sys
execute(["manage.py", "runserver", "--settings=alternate", "--pythonpath=" + sys.argv[1]])
'''
    environment = {**os.environ, "RUN_MAIN": "true", "PYTHONPATH": os.pathsep.join(sys.path)}
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DJANGO_COMPOSE_MODE", None)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "recoverable-dispatch" in result.stdout
    assert "first-enter" not in result.stdout


def test_server_launcher_unwinds_after_failure_and_logs_once(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "plain" / "logging.py").write_text('''def configure(config):
    print("logging-configured")
''')
    settings_file = tmp_path / "alternate.py"
    settings_file.write_text(
        settings_file.read_text() + '\nLOGGING_CONFIG = "plain.logging.configure"\nLOGGING = {"version": 1}\n'
    )
    command = tmp_path / "plain" / "management" / "commands" / "probe.py"
    command.write_text(command.read_text().replace('print("dispatched")', 'raise RuntimeError("command failed")'))
    result = _invoke(tmp_path, "probe", "--settings=alternate", f"--pythonpath={tmp_path}")
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        "logging-configured", "first-enter probe", "second-enter", "second-exit", "first-exit"
    ]


def test_native_launcher_extension_receives_server_utility(monkeypatch):
    seen = []

    def execute(argv=None, *, utility_class):
        seen.append((argv, utility_class))

    monkeypatch.setattr(management.compose_management, "execute", execute)
    argv = ["manage.py", "check"]
    management.execute(argv)
    assert seen == [(argv, management.ServerManagementUtility)]


@pytest.fixture
def native_context(monkeypatch):
    ready = Event()
    ready.set()
    monkeypatch.setattr(management, "apps", SimpleNamespace(ready_event=ready))
    monkeypatch.setattr(management, "settings", SimpleNamespace(ANGEE_SERVER_COMMAND_CONTEXT=("context.factory",)))
    return ready


@pytest.mark.parametrize("selected", [None, "replica"])
def test_native_parser_custom_action_and_dispatch_run_once(native_context, monkeypatch, selected):
    events = []
    seen = []

    class Database(Action):
        def __call__(self, parser, namespace, value, option_string=None):
            events.append("parse")
            namespace.database = value.upper()

    class Command(BaseCommand):
        requires_system_checks = []

        def add_arguments(self, parser):
            parser.add_argument("--database", default="default", action=Database)

        def run_from_argv(self, argv):
            events.append("argv-before")
            super().run_from_argv(argv)
            events.append("argv-after")

        def execute(self, *args, **options):
            events.append("execute-before")
            result = super().execute(*args, **options)
            events.append("execute-after")
            return result

        def handle(self, *args, **options):
            seen.append(options["database"])
            events.append("handle")

    @contextmanager
    def factory(command, argv, options):
        assert command == "probe"
        assert argv == tuple(arguments)
        assert options["database"] == (selected.upper() if selected else "default")
        with pytest.raises(TypeError):
            options["database"] = "changed"
        events.append("context-enter")
        try:
            yield
        finally:
            events.append("context-exit")

    monkeypatch.setattr(management, "import_string", lambda path: factory)
    monkeypatch.setattr(connections, "close_all", lambda: events.append("connections-close"))
    command = Command()
    arguments = ["manage.py", "probe", *(["--database", selected] if selected else [])]
    management._Dispatch(command).run_from_argv(arguments)
    assert seen == [selected.upper() if selected else "default"]
    assert events == [
        "argv-before",
        *(["parse"] if selected else []),
        "context-enter",
        "execute-before",
        "handle",
        "execute-after",
        "context-exit",
        "connections-close",
        "argv-after",
    ]
    assert "execute" not in vars(command)


@pytest.mark.parametrize("arguments", [["--help"], ["--version"], ["--unknown"]])
@pytest.mark.parametrize("preexisting", [False, True])
def test_native_early_exit_never_enters_context_and_restores_execute(
    native_context, monkeypatch, arguments, preexisting
):
    class Command(BaseCommand):
        requires_system_checks = []

    def forbidden(path):
        raise AssertionError("parser exit entered contexts")

    monkeypatch.setattr(management, "import_string", forbidden)
    command = Command()
    original = command.execute
    if preexisting:
        command.execute = original
    with pytest.raises(SystemExit):
        management._Dispatch(command).run_from_argv(["manage.py", "probe", *arguments])
    if preexisting:
        assert vars(command)["execute"] is original
    else:
        assert "execute" not in vars(command)


def test_context_command_error_uses_native_formatting_and_connection_cleanup(native_context, monkeypatch):
    events = []

    @contextmanager
    def factory(command, argv, options):
        events.append("context")
        raise CommandError("attribution refused", returncode=7)
        yield

    class Command(BaseCommand):
        requires_system_checks = []

        def handle(self, *args, **options):
            raise AssertionError("handler ran after failed attribution")

    stderr = StringIO()
    command = Command(stderr=stderr)
    monkeypatch.setattr(management, "import_string", lambda path: factory)
    monkeypatch.setattr(connections, "close_all", lambda: events.append("connections-close"))
    with pytest.raises(SystemExit) as error:
        management._Dispatch(command).run_from_argv(["manage.py", "probe"])
    assert error.value.code == 7
    assert stderr.getvalue() == "CommandError: attribution refused\n"
    assert events == ["context", "connections-close"]
    assert "execute" not in vars(command)


@pytest.mark.parametrize("failure", [RuntimeError("failed"), KeyboardInterrupt()])
def test_existing_instance_execute_restored_after_failure(native_context, monkeypatch, failure):
    events = []

    @contextmanager
    def factory(command, argv, options):
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    class Command(BaseCommand):
        requires_system_checks = []

    command = Command()

    def original(*args, **options):
        raise failure

    command.execute = original
    monkeypatch.setattr(management, "import_string", lambda path: factory)
    monkeypatch.setattr(connections, "close_all", lambda: events.append("connections-close"))
    with pytest.raises(type(failure)):
        management._Dispatch(command).run_from_argv(["manage.py", "probe"])
    assert vars(command)["execute"] is original
    assert events == ["enter", "exit", "connections-close"]


def test_custom_run_from_argv_bypassing_execute_has_no_context(native_context, monkeypatch):
    class Command(BaseCommand):
        def run_from_argv(self, argv):
            return "custom-dispatch"

    def forbidden(path):
        raise AssertionError("context injected without native execution boundary")

    monkeypatch.setattr(management, "import_string", forbidden)
    command = Command()
    assert management._Dispatch(command).run_from_argv(["manage.py", "probe"]) == "custom-dispatch"
    assert "execute" not in vars(command)
