"""Run the shipped archive graphs against fresh generated models."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_archive_import(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "archive-workflow.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable, str(root / "tests/composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            "--app", "angee.workflows_integrate", "--no-examples",
            "--action", "tests", "--test-label", "tests.native_integrate_workflow",
            "--output", str(report),
        ], cwd=root, env=env, capture_output=True, text=True, timeout=240, check=False,
    )
    assert result.returncode == 0, f"composed archive import failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "sqlite"}
