"""Executable contract for the advisory owner-map ``PreToolUse`` hook.

Runs the script as Claude Code would: JSON on stdin, JSON or nothing on stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".agents/tools/owner-map-guard.py"


def run_guard(tmp_path: Path, stdin: str, project_dir: Path | None = None) -> str:
    """Invoke the hook with an isolated marker directory and return its stdout."""
    env = {"PATH": "", "TMPDIR": str(tmp_path)}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    result = subprocess.run(
        [sys.executable, "-B", str(GUARD)],
        input=stdin,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def event(file_path: str, cwd: Path = ROOT) -> str:
    return json.dumps(
        {
            "session_id": "s1",
            "cwd": str(cwd),
            "tool_name": "Edit",
            "tool_input": {"file_path": file_path},
        }
    )


def reminded(stdout: str) -> bool:
    return "angee-design" in json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


def test_first_source_edit_reminds_once_per_session(tmp_path: Path) -> None:
    context = json.loads(run_guard(tmp_path, event("src/angee/base/models/base.py")))
    assert context["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "angee-design" in context["hookSpecificOutput"]["additionalContext"]
    assert run_guard(tmp_path, event("packages/ui/src/index.ts")) == ""


def test_relative_paths_resolve_against_a_nested_cwd_and_classify_from_the_project_root(tmp_path: Path) -> None:
    assert reminded(run_guard(tmp_path, event("models.py", cwd=ROOT / "src/angee/tags")))


def test_configured_project_root_classifies_edits_started_outside_the_repository(tmp_path: Path) -> None:
    stdin = event(str(ROOT / "packages/ui/src/index.ts"), cwd=tmp_path)
    assert run_guard(tmp_path, stdin) == ""
    assert reminded(run_guard(tmp_path, stdin, project_dir=ROOT))


@pytest.mark.parametrize(
    "file_path",
    [
        "tests/test_base.py",
        "src/angee/base/README.md",
        "docs/stack.md",
        "src/angee/tags/web/src/views/TagsPage.test.tsx",
        "src/angee/tags/web/src/views/TagsPage.native.test.tsx",
        "packages/ui/src/views/list.spec.ts",
        "packages/ui/src/views/Button.stories.tsx",
    ],
)
def test_non_source_edits_are_silent_and_keep_the_reminder(tmp_path: Path, file_path: str) -> None:
    assert run_guard(tmp_path, event(file_path)) == ""
    assert reminded(run_guard(tmp_path, event("src/angee/tags/models.py")))


def test_malformed_input_is_silent(tmp_path: Path) -> None:
    assert run_guard(tmp_path, "{not json") == ""
