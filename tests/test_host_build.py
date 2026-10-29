"""The explicit host build worker is independent of generated settings and Hatch."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from angee import build


def test_build_entrypoint_passes_explicit_project_inputs(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(
        build, "build_runtime", lambda root, output, **options: captured.append((root, output, options)),
    )
    monkeypatch.setattr(sys, "argv", [
        "angee.build", "--root", str(tmp_path), "--output", str(tmp_path / "runtime"), "--check-history",
    ])
    build.main()
    assert captured == [(tmp_path, tmp_path / "runtime", {"check_history": True})]


def test_first_build_help_does_not_load_generated_settings(tmp_path):
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(build.__file__).resolve().parent.parent),
        "DJANGO_SETTINGS_MODULE": "missing_generated_runtime.settings",
    }
    result = subprocess.run(
        [sys.executable, "-m", "angee.build", "--help"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--check-history" in result.stdout
    assert "--root" in result.stdout
