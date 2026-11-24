"""Exercise the public-repository guard against actual private tracked paths."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["directory", "symlink", "public"])
def test_private_path_ci_guard_matches_tracked_content(tmp_path: Path, kind: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["guards"]["steps"]
    guard = next(step["run"] for step in steps if step.get("name") == "Reject tracked .work/ paths")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    if kind == "symlink":
        (tmp_path / ".work").symlink_to("../private-history")
        tracked = ".work"
    else:
        tracked = ".work/notes/private.md" if kind == "directory" else "docs/guidelines.md"
        target = tmp_path / tracked
        target.parent.mkdir(parents=True)
        target.write_text("fixture\n")
    subprocess.run(["git", "add", "--", tracked], cwd=tmp_path, check=True, capture_output=True)
    result = subprocess.run(["sh", "-c", guard], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == (0 if kind == "public" else 1), result.stdout + result.stderr


def test_legacy_private_checkout_paths_remain_ignored(tmp_path: Path) -> None:
    """Check the ignore contract without traversing a materialized work-state link."""

    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=tmp_path,
        input=".work\n.work/notes/private.md\ndocs/guidelines.md\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == {".work", ".work/notes/private.md"}
