"""Dispatch the example's new cases through the shared composed-host script."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.composed_host import COMPOSED_TEST_TIMEOUT, assert_composed_report


@pytest.mark.parametrize("case", ("NotesOwnerCampaign", "NotesOwnerDenormalizedCampaign"))
def test_example_note_owner_gate(tmp_path, case):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "notes-owner-campaign.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    # The existing notes wrapper uses this host's default example discovery.
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests/composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--action",
            "tests",
            "--test-label",
            f"tests.native_notes_owner_campaign.{case}",
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
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert_composed_report(report, vendor="sqlite")
