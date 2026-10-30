"""Authored project templates use the native launcher before output exists."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_TEMPLATE = ROOT / "templates/projects/web/template"


def test_project_entrypoints_dispatch_to_native_owners() -> None:
    """The authored host delegates configuration and dispatch without a build wrapper."""

    launcher = ast.parse((PROJECT_TEMPLATE / "manage.py.jinja").read_text())
    settings = ast.parse((PROJECT_TEMPLATE / "settings.py").read_text())
    assert any(isinstance(node, ast.ImportFrom) and node.module == "django_compose.management"
               and [alias.name for alias in node.names] == ["execute"] for node in ast.walk(launcher))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "execute"
               for node in ast.walk(launcher))
    assert any(isinstance(node, ast.ImportFrom) and node.module == "angee.base.conf"
               for node in ast.walk(settings))


def test_first_compose_help_does_not_load_generated_settings(tmp_path: Path) -> None:
    """The actual project launcher can explain compose without importing settings."""

    # The only template expression is inside the docstring, so the source is executable unchanged.
    launcher = tmp_path / "manage.py"
    launcher.write_text((PROJECT_TEMPLATE / "manage.py.jinja").read_text())
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "missing_generated_runtime.settings"}
    environment.pop("DJANGO_COMPOSE_MODE", None)
    result = subprocess.run(
        [sys.executable, str(launcher), "compose", "--help"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--check" in result.stdout
    assert "--if-stale" in result.stdout
