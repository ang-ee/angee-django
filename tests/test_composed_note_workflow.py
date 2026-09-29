"""Run the consumer-owned workflow contracts against a fresh composed host."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django.db import connection
from hatch_angee import discover


def test_composed_note_workflow(tmp_path: Path) -> None:
    """Run the installed graph using the configured database backend."""

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "note-workflow.json"
    postgresql = connection.vendor == "postgresql"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    # L4: extraction's shipped map is the only resource awaiting engine support.
    apps = sorted(
        manifest.name for _, manifest in discover([root / "addons", root / "examples" / "addons"])
        if manifest.name != "angee.workflows_extraction"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests" / "composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            *(argument for app in apps for argument in ("--app", app)),
            "--action", "tests",
            "--test-label", "example.notes.tests.test_workflow_steps",
            "--output", str(report),
            *(["--test-postgresql"] if postgresql else []),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, f"composed note workflow failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "postgresql" if postgresql else "sqlite"}
