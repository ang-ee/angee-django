"""Validate every declared resource value against its actual emitted Django field."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_declared_resource_values_are_accepted_by_their_model_field(tmp_path: Path) -> None:
    """All local addon fixtures resolve through a fresh composed Django registry.

    The subprocess runs the real settings composer and generated model imports.
    This keeps the broad fixture sweep independent of pytest's hand-built models,
    while Django alone resolves donor and multi-table parent fields. Relation
    cells contain xref handles, so the loader owns their separate validation.
    """

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "resources.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests" / "composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            "--output", str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"fresh composed host failed:\n{result.stdout}\n{result.stderr}"
    sweep = json.loads(report.read_text())
    assert sweep["checked_values"], "no resource fixture values were discovered"
    assert not sweep["failures"], (
        "resource fixtures declare values their model rejects:\n" + "\n".join(sweep["failures"])
    )
