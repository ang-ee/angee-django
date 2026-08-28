"""Contracts for selecting the stack that owns agent-driven workspace lifecycle."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_skill_prefers_an_existing_ancestor_stack_root() -> None:
    """A containing stack owns workspaces before a checkout-local dev overlay."""

    skill = (ROOT / ".agents/skills/angee-workspace/SKILL.md").read_text(
        encoding="utf-8"
    )

    for contract in (
        'if test -f "$candidate/angee.yaml"; then',
        "walk up from the current\ndirectory",
        'angee --root "$angee_root" ws create',
        "do not run `angee init`",
    ):
        assert contract in skill

    # The checkout-local .angee/ dev overlay died with angee v0.9.0: an
    # ancestor stack is the only owner, and a workspace root is not a git
    # repository, so resolution never starts from git rev-parse.
    assert ".angee/angee.yaml" not in skill
    assert "git rev-parse --show-toplevel) || exit 1" not in skill
    assert "Run Angee commands from the repository root" not in skill


def test_repository_agents_defers_stack_lifecycle_to_an_existing_parent_root() -> None:
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())

    assert "An existing current or ancestor `angee.yaml` owns" in agents
    assert "Never initialize a stack under a source checkout" in agents
    assert 'angee --root "$angee_root" ws' in agents


def test_setup_docs_guard_init_and_target_the_resolved_workspace_root() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    guide = " ".join(
        (ROOT / "docs/howto/getstarted.md").read_text(encoding="utf-8").split()
    )

    templates = " ".join(
        (ROOT / "templates/README.md").read_text(encoding="utf-8").split()
    )
    for document in (readme, guide, templates):
        assert "existing current or ancestor `angee.yaml`" in document
        assert "never initialize a stack under a source checkout" in document

    assert 'angee --root "$angee_root" ws create' in guide
    assert 'cd "$angee_root/workspaces/my-feature"' in guide
