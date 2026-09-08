"""Run the executable Note workflow against real generated models."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_note_workflow(tmp_path: Path) -> None:
    """The consumer graph validates, waits for approval, and publishes its Note."""

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "note-workflow.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests" / "composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--action",
            "tests",
            "--test-label",
            "example.notes.tests.test_workflow_steps",
            "--output",
            str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, f"composed Note workflow failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0}
