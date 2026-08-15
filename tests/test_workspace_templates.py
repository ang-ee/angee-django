"""Regression coverage for workspace template contracts."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC_COPIER = ROOT / "templates" / "workspaces" / "src" / "copier.yml"
SRC_CODE_WORKSPACE = ROOT / "templates" / "workspaces" / "src" / "template" / "angee.code-workspace"

# Every framework source repo the src workspace materializes as a worktree slot.
SRC_SLOTS = {
    "angee-django",
    "angee-react",
    "angee-base",
    "angee-messaging-bridges",
    "angee-examples",
    "angee-templates",
    "angee-operator",
}


def test_src_workspace_materializes_every_framework_repo_as_a_worktree_slot() -> None:
    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))
    sources = manifest["_angee"]["sources"]

    for slot in SRC_SLOTS:
        record = sources[slot]
        assert record["source"] == slot
        assert record["mode"] == "worktree"
        assert record["branch"] == "${inputs.branch_prefix}/${name | slug}"
        assert record["subpath"] == slot

    # The opt-in repos skip when a base-profile stack declares no source.
    for slot in ("angee-messaging-bridges", "angee-examples"):
        assert sources[slot]["optional"] is True

    # angee-django can pin a ratified branch; every other slot follows base_ref.
    assert sources["angee-django"]["ref"] == "${inputs.angee_django_ref}"
    for slot in SRC_SLOTS - {"angee-django"}:
        assert sources[slot]["ref"] == "${inputs.base_ref}"


def test_src_workspace_preserves_its_claude_symlink() -> None:
    """The template ships CLAUDE.md -> AGENTS.md; without _preserve_symlinks
    the renderer resolves it against a symlinked template root and dies with
    "points outside template root" (the P7 ws-update bug)."""

    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))
    assert manifest["_preserve_symlinks"] is True
    claude = SRC_COPIER.parent / "template" / "CLAUDE.md"
    assert claude.is_symlink()
    assert str(claude.readlink()) == "AGENTS.md"


def test_src_workspace_work_state_is_opt_in_via_source_name_input() -> None:
    """A clean-machine stack has no private work-state source; the slot is opt-in.

    The slot's source name comes from the work_state_source input; the default is
    empty, which the operator skips at create time — the workspace must never
    require a private repository to materialize.
    """

    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))

    work_state_input = manifest["_angee"]["inputs"]["work_state_source"]
    assert work_state_input["type"] == "str"
    assert work_state_input["default"] == ""

    work_state = manifest["_angee"]["sources"]["work-state"]
    assert work_state == {"source": "${inputs.work_state_source}", "subpath": ".work", "optional": True}

    copier_question = manifest["work_state_source"]
    assert copier_question["type"] == "str"
    assert copier_question["default"] == ""


def test_src_workspace_code_workspace_lists_every_slot() -> None:
    """The rendered code-workspace file opens all slots (and .work) side by side."""

    text = SRC_CODE_WORKSPACE.read_text(encoding="utf-8")
    for slot in SRC_SLOTS | {".work"}:
        assert f'"path": "{slot}"' in text
