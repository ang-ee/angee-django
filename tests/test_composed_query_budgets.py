"""Run native addon query budgets against real generated models in CI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_addon_query_budgets(tmp_path: Path) -> None:
    """Keep actual HTTP/model query regressions in the normal pytest gate."""

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "query-budgets.json"
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
            "example.notes.tests.test_query_budgets",
            "--test-label",
            "angee.projects.tests.test_query_budgets",
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
    assert result.returncode == 0, f"composed query budgets failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0}
