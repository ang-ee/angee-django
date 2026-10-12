"""Compose the comment reply flow against the selected database lane."""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.db import connection


def test_composed_workflows_posts(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "workflows-posts.json"
    postgresql = connection.vendor == "postgresql"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable, str(root / "tests/composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            "--app", "angee.workflows_posts", "--app", "angee.workflows_messaging",
            "--app", "angee.agents_runtime_pydantic", "--app", "angee.knowledge", "--no-examples",
            "--action", "tests", "--test-label", "tests.native_workflows_posts",
            "--output", str(report), *(["--test-postgresql"] if postgresql else []),
        ],
        cwd=root, env=env, capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, f"composed comment replies failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "postgresql" if postgresql else "sqlite"}
