"""Independent record decision composition and implementation registration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from graphql import build_schema, get_named_type


def test_decisions_compose_independently_with_inbox_read_resources(tmp_path: Path) -> None:
    """Compile only the decisions dependency closure in a fresh Django process."""
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DATABASE_URL", None)
    composed = {}
    for action in ("snapshot", "schemas"):
        report = tmp_path / f"{action}.json"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "tests/composed_host.py"),
                "--source-root",
                str(root),
                "--runtime-dir",
                str(tmp_path / action / "runtime"),
                "--app",
                "angee.decisions",
                "--no-examples",
                "--action",
                action,
                "--output",
                str(report),
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, f"Decision composition failed:\n{result.stdout}\n{result.stderr}"
        composed[action] = json.loads(report.read_text())
    assert not any(name.startswith("workflows.") for name in composed["snapshot"])
    assert {name for name in composed["snapshot"] if name.startswith("decisions.")} == {
        "decisions.decision",
        "decisions.decisionrecord",
    }
    assert all(not model["checks"] for model in composed["snapshot"].values())
    schema = build_schema(composed["schemas"]["console"])
    assert schema.query_type is not None
    assert {"decisions", "decisions_by_pk", "decision_records"} <= schema.query_type.fields.keys()
    assert "open_decisions" not in schema.query_type.fields
    assert schema.mutation_type is not None
    mutations = schema.mutation_type.fields
    assert "decide" in mutations
    assert set(mutations["decide"].args) == {"id", "revision", "chosen", "values"}
    assert "verdict_values" in get_named_type(schema.query_type.fields["decisions"].type).fields
    assert not any(name.startswith(("insert_decision", "update_decision", "delete_decision")) for name in mutations)
    filters = schema.query_type.fields["decisions"].args["where"].type
    assert {"assignees", "requester"} <= filters.fields.keys()
