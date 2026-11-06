"""Exercise compiler support for donors on selected abstract contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.composed_host import COMPOSED_TEST_TIMEOUT


def test_iam_root_composes_base_abstract_contracts(tmp_path: Path) -> None:
    """IAM's append-only donor resolves its base contract without other roots."""

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "model-snapshot.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests" / "composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            "--app", "angee.iam",
            "--no-examples",
            "--action", "snapshot",
            "--output", str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=COMPOSED_TEST_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, f"IAM composition failed:\n{result.stdout}\n{result.stderr}"
    assert "iam.user" in json.loads(report.read_text())
