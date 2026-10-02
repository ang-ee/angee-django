"""Exercise notes ownership using the repository's generated-model host."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_notes_preserve_attribution_across_ownership_transfer(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "notes-ownership.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable, str(root / "tests/composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"), "--action", "tests",
            "--test-label", "tests.notes_ownership_cases", "--output", str(report),
        ],
        cwd=root, env=env, capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "sqlite"}
