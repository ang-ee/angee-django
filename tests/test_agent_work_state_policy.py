"""Focused contracts for shared private agent work-state routing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agents_routes_work_state_into_dot_work() -> None:
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())

    assert "design specs: `.work/plans/specs/`" in agents
    assert "plans: `.work/plans/`" in agents
    assert "notes: `.work/notes/`" in agents
    assert "handovers: `.work/handovers/`" in agents
    assert "`docs/superpowers/**` are overridden and forbidden" in agents


def test_workspace_skill_passes_canonical_work_state_path() -> None:
    skill = (ROOT / ".agents/skills/angee-workspace/SKILL.md").read_text(
        encoding="utf-8"
    )

    for contract in (
        'test -L "$repo_root/.work" || exit 1',
        'work_state_path=$(cd "$repo_root/.work" && pwd -P) || exit 1',
        'git -C "$work_state_path" rev-parse --show-toplevel',
        'test "$work_state_top" = "$work_state_path" || exit 1',
        'basename "$work_state_path"',
        '--input work_state_path="$work_state_path"',
        "Resolved work-state path.",
    ):
        assert contract in skill
