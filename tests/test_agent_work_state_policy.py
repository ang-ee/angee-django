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
        # Work-state is wired by stack source NAME; the src template's
        # work-state slot materializes <workspace>/.work as its own clone.
        "wired by stack source NAME",
        "--input work_state_source=",
        "work-state` slot is a slot like any other",
        "ws source push <workspace> work-state",
        "do not fall back to `docs/superpowers`",
    ):
        assert contract in skill
