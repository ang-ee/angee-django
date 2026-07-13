"""Contract tests for the repository's private agent work-state routing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repository_routes_agent_artifacts_into_work() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    prose = " ".join(agents.split())

    assert "design specs: `.work/plans/specs/`" in prose
    assert "plans: `.work/plans/`" in prose
    assert "notes: `.work/notes/`" in prose
    assert "handovers: `.work/handovers/`" in prose
    assert (
        "Global skill defaults such as `docs/superpowers/**` are overridden and forbidden in this repository."
    ) in prose


def test_workspace_creation_passes_validated_canonical_work_state() -> None:
    skill = (ROOT / ".agents/skills/angee-workspace/SKILL.md").read_text(encoding="utf-8")

    for required_contract in (
        "repo_root=$(git rev-parse --show-toplevel) || exit 1",
        'test -L "$repo_root/.work" || exit 1',
        'work_state_path=$(cd "$repo_root/.work" && pwd -P) || exit 1',
        'work_state_top=$(git -C "$work_state_path" rev-parse --show-toplevel) || exit 1',
        'test "$work_state_top" = "$work_state_path" || exit 1',
        'test "$(basename "$work_state_path")" != "$(basename "$repo_root")" || exit 1',
        '--input work_state_path="$work_state_path"',
        "Resolved work-state path.",
    ):
        assert required_contract in skill

    assert "do not fall back to `docs/superpowers`" in " ".join(skill.split())
