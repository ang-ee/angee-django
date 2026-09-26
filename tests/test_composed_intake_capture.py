"""Verify intake capture on emitted models, not a mocked registry."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_intake_capture(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "intake-capture.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests/composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--app",
            "angee.intake",
            "--no-examples",
            "--action",
            "tests",
            "--test-label",
            "tests.native_intake_capture",
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
    assert result.returncode == 0, f"composed intake capture failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0}
