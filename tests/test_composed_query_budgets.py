"""Run native addon query budgets against real generated models in CI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.db import connection

from tests.composed_host import COMPOSED_TEST_TIMEOUT


@pytest.mark.parametrize("postgresql", [False, True], ids=["sqlite", "postgresql"])
def test_composed_addon_query_budgets(tmp_path: Path, postgresql: bool) -> None:
    """Keep actual HTTP/model query regressions in the normal pytest gate."""

    if postgresql and connection.vendor != "postgresql":
        pytest.skip("PostgreSQL planner budget")
    labels = (
        ("tests.native_graphql_document_budgets.ConsoleDocumentBudgetTests.test_member_reads_planner_memory",)
        if postgresql
        else (
            "example.notes.tests.test_query_budgets",
            "angee.projects.tests.test_query_budgets",
            "tests.native_graphql_document_budgets.ConsoleDocumentBudgetTests."
            "test_console_reads_stay_within_their_fixed_ceilings",
        )
    )
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
            *(["--test-postgresql"] if postgresql else []),
            *(argument for label in labels for argument in ("--test-label", label)),
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
    assert result.returncode == 0, f"composed query budgets failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "postgresql" if postgresql else "sqlite"}
