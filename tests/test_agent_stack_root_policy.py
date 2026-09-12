"""Exercise the documented stack resolver without invoking stack lifecycle."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def resolver_script() -> str:
    skill = (ROOT / ".agents/skills/angee-workspace/SKILL.md").read_text()
    blocks = re.findall(r"```sh\n(.*?)\n```", skill, re.DOTALL)
    return next(block for block in blocks if block.startswith("angee_root="))


@pytest.mark.parametrize("nested_stack", [False, True])
def test_documented_resolver_selects_nearest_stack_from_source_slot(tmp_path: Path, nested_stack: bool) -> None:
    outer = tmp_path / "parent stack"
    inner = outer / "child stack"
    slot = inner / "workspaces/topic/angee"
    slot.mkdir(parents=True)
    (outer / "angee.yaml").write_text("{}\n")
    if nested_stack:
        (inner / "angee.yaml").write_text("{}\n")
    # A checkout marker and obsolete local overlay must not become stack roots.
    (slot / ".git").write_text("gitdir: unused\n")
    (slot / ".angee").mkdir()
    (slot / ".angee/angee.yaml").write_text("{}\n")
    result = subprocess.run(
        ["sh", "-c", resolver_script() + '\nprintf "%s" "$angee_root"'],
        cwd=slot,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout) == (inner if nested_stack else outer).resolve()


def test_documented_resolver_without_stack_stops_without_creating_one(tmp_path: Path) -> None:
    result = subprocess.run(["sh", "-c", resolver_script()], cwd=tmp_path, capture_output=True, check=False)
    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == []
