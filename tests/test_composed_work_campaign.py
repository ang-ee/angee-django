"""Run contracts needing the complete Task graph in the shared composed host."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_work_contracts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "work-campaign.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [sys.executable, str(root / "tests/composed_host.py"), "--runtime-dir", str(tmp_path / "runtime"),
         "--app", "angee.work", "--no-examples", "--action", "tests",
         "--test-label", "tests.native_work_campaign", "--output", str(report)],
        cwd=root, env=env, capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, f"composed work campaign failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0}
