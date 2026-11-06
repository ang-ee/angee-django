"""Compose every installed addon to catch cross-addon model field collisions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django_compose.components import installed_components

from tests.composed_host import COMPOSED_TEST_TIMEOUT


def test_installed_graph_composes_party_once(tmp_path: Path) -> None:
    """A test donor and installed product donors can extend Party together."""

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "model-snapshot.json"
    apps = sorted(installed_components("angee.addons").providers)
    assert {"angee.parties", "tests.blankchoices"} <= set(apps)
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests" / "composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            *(argument for app in apps for argument in ("--app", app)),
            "--action",
            "snapshot",
            "--output",
            str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=COMPOSED_TEST_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, f"full graph composition failed:\n{result.stdout}\n{result.stderr}"
    assert "blank_choice_country" in json.loads(report.read_text())["parties.party"]["fields"]
