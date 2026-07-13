"""Contract tests for the repository's private agent work-state routing."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORK_STATE_INPUT = '--input work_state_path="$work_state_path"'
WORK_STATE_SETUP = (
    "repo_root=$(git rev-parse --show-toplevel) || exit 1",
    'test -L "$repo_root/.work" || exit 1',
    'work_state_path=$(cd "$repo_root/.work" && pwd -P) || exit 1',
)
WORK_STATE_GIT_GUARDS = (
    'work_state_top=$(git -C "$work_state_path" rev-parse --show-toplevel) || exit 1',
    'test "$work_state_top" = "$work_state_path" || exit 1',
)
WORK_STATE_BASENAME_GUARD = 'test "$(basename "$work_state_path")" != "$(basename "$repo_root")" || exit 1'
WORK_STATE_GUARDS = (*WORK_STATE_SETUP, *WORK_STATE_GIT_GUARDS, WORK_STATE_BASENAME_GUARD)
WORKSPACE_OWNER = ".agents/skills/angee-workspace/SKILL.md"


def _tracked_markdown() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = [ROOT / relative_path for relative_path in result.stdout.splitlines()]
    return [path for path in paths if path.is_file()]


def _shell_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    block: list[str] | None = None

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped in {"```bash", "```sh", "```shell"}:
            block = []
            continue
        if block is not None and stripped == "```":
            blocks.append("\n".join(block))
            block = None
            continue
        if block is not None:
            block.append(line)

    return blocks


def _shell_commands(shell_block: str) -> list[str]:
    commands: list[str] = []
    continuation: list[str] = []

    for line in shell_block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        continuation.append(stripped.removesuffix("\\").rstrip())
        if not stripped.endswith("\\"):
            commands.append(" ".join(continuation))
            continuation = []

    return commands


def _dev_workspace_violations(markdown: str) -> list[str]:
    violations: list[str] = []

    for shell_block in _shell_blocks(markdown):
        validates_work_state = all(guard in shell_block for guard in WORK_STATE_GUARDS)
        for command in _shell_commands(shell_block):
            if "angee ws create" not in command or "--template dev" not in command:
                continue
            if WORK_STATE_INPUT not in command or not validates_work_state:
                violations.append(command)

    return violations


def _shell_block(*lines: str) -> str:
    return "```sh\n" + "\n".join(lines) + "\n```\n"


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


@pytest.mark.parametrize(
    "markdown",
    [
        _shell_block(
            *WORK_STATE_SETUP,
            WORK_STATE_BASENAME_GUARD,
            f"angee ws create demo --template dev {WORK_STATE_INPUT}",
        ),
        _shell_block(
            *WORK_STATE_SETUP,
            *WORK_STATE_GIT_GUARDS,
            f"angee ws create demo --template dev {WORK_STATE_INPUT}",
        ),
        _shell_block(*WORK_STATE_GUARDS) + _shell_block(f"angee ws create demo --template dev {WORK_STATE_INPUT}"),
    ],
    ids=["missing-git-top-level", "missing-basename-check", "disconnected-setup"],
)
def test_dev_workspace_validation_rejects_unsafe_synthetic_flows(markdown: str) -> None:
    assert _dev_workspace_violations(markdown)


def test_documented_dev_workspace_creation_flows_pass_validated_work_state_path() -> None:
    violations: list[str] = []

    for path in _tracked_markdown():
        if path.relative_to(ROOT).as_posix() == WORKSPACE_OWNER:
            continue
        markdown = path.read_text(encoding="utf-8")
        violations.extend(f"{path.relative_to(ROOT)}: {command}" for command in _dev_workspace_violations(markdown))

    assert not violations, "Invalid dev-workspace creation flows:\n" + "\n".join(violations)
