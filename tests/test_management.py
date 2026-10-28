"""Native management entrypoints compose optional host contexts."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from angee import management


def test_management_import_and_core_command_do_not_load_optional_owners() -> None:
    """A core-only host needs neither provenance, REBAC nor the compiler to dispatch."""

    script = """
import sys
from django.conf import settings
settings.configure(SECRET_KEY="core-only-management")
from angee import management
seen = []
management.execute_from_command_line = lambda argv: seen.append(argv)
management.main(["manage.py", "check"])
assert seen == [["manage.py", "check"]]
for forbidden in ("angee.provenance", "angee.compose", "rebac"):
    assert not any(name == forbidden or name.startswith(forbidden + ".") for name in sys.modules), forbidden
import angee.management.bootstrap
management.main(["manage.py", "check"])
assert seen == [["manage.py", "check"], ["manage.py", "check"]]
"""
    environment = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fail", [False, True])
def test_management_context_wraps_dispatch_and_unwinds_on_failure(
    settings: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: bool,
) -> None:
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    events: list[object] = []

    @contextmanager
    def context(command: str) -> Iterator[None]:
        events.append(("enter", command))
        try:
            yield
        finally:
            events.append(("exit", command))

    hook = ModuleType("management_context_probe")
    hook.open_context = context  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, hook.__name__, hook)
    settings.ANGEE_MANAGEMENT_CONTEXT = f"{hook.__name__}.open_context"

    def dispatch(argv: list[str]) -> None:
        events.append(("dispatch", argv[1]))
        if fail:
            raise RuntimeError("command failed")

    monkeypatch.setattr(management, "execute_from_command_line", dispatch)
    argv = [str(tmp_path / "manage.py"), "migrate"]
    if fail:
        with pytest.raises(RuntimeError, match="command failed"):
            management.main(argv)
    else:
        management.main(argv)
    assert events == [("enter", "migrate"), ("dispatch", "migrate"), ("exit", "migrate")]


@pytest.mark.parametrize("command", sorted(management.SERVER_COMMANDS))
def test_long_running_commands_keep_their_own_contexts(
    settings: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str,
) -> None:
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    settings.ANGEE_MANAGEMENT_CONTEXT = "missing_optional_addon.open_context"
    seen = []
    monkeypatch.setattr(management, "execute_from_command_line", lambda argv: seen.append(argv))
    argv = [str(tmp_path / "manage.py"), command]

    management.main(argv)

    assert seen == [argv]
