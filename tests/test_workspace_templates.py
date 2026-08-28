"""Regression coverage for workspace template contracts."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC_COPIER = ROOT / "templates" / "workspaces" / "src" / "copier.yml"
SRC_CODE_WORKSPACE = ROOT / "templates" / "workspaces" / "src" / "template" / "angee.code-workspace"
SRC_AGENTS = ROOT / "templates" / "workspaces" / "src" / "template" / "AGENTS.md"

SRC_SLOTS = {"angee", "angee-messaging-bridges", "angee-arp"}
OPTIONAL_SLOTS = {"angee-messaging-bridges", "angee-arp"}


def test_src_workspace_materializes_one_framework_worktree_and_optional_externals() -> None:
    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))
    sources = manifest["_angee"]["sources"]
    assert set(sources) == SRC_SLOTS | {"work-state"}

    for slot in SRC_SLOTS:
        record = sources[slot]
        assert record["source"] == slot
        assert record["mode"] == "worktree"
        assert record["branch"] == "${inputs.branch_prefix}/${name | slug}"
        assert record["subpath"] == slot

    assert "optional" not in sources["angee"]
    for slot in OPTIONAL_SLOTS:
        assert sources[slot]["optional"] is True
    assert sources["angee"]["ref"] == "${inputs.angee_ref}"
    for slot in OPTIONAL_SLOTS:
        assert sources[slot]["ref"] == "main"

    inputs = manifest["_angee"]["inputs"]
    assert set(inputs) == {
        "agent_name",
        "mcp_json",
        "branch_prefix",
        "angee_ref",
        "work_state_source",
    }
    assert inputs["angee_ref"] == {
        "type": "str",
        "default": "main",
        "help": "Mainline ref for the consolidated framework source slot.",
    }
    assert manifest["angee_ref"] == {"type": "str", "default": "main"}


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
    """The rendered code-workspace file opens exactly the new slots and work state."""

    text = SRC_CODE_WORKSPACE.read_text(encoding="utf-8")
    assert set(re.findall(r'"path": "([^"]+)"', text)) == SRC_SLOTS | {".work"}


def test_src_workspace_agents_describes_the_consolidated_framework_slot() -> None:
    text = SRC_AGENTS.read_text(encoding="utf-8")

    assert "**`angee/`** — the consolidated framework repository" in text
    for area in ("`angee/`", "`addons/`", "`packages/`", "`examples/`", "`templates/`"):
        assert area in text
    assert "consumes their PyPI releases" in text
    assert "never `git checkout`" in text
    assert "Never `pnpm install` inside a slot" in text
    assert "commit and push continuously" in text


def test_src_workspace_declares_agent_inputs_and_fail_loud_instance_naming() -> None:
    """Agent-created src workspaces derive names without a mass-name fallback."""

    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))

    for inputs in (manifest["_angee"]["inputs"], manifest):
        assert inputs["agent_name"]["type"] == "str"
        assert inputs["agent_name"]["default"] == ""
        assert inputs["mcp_json"]["type"] == "str"
        assert inputs["mcp_json"]["default"] == '{"mcpServers": {}}'

    assert manifest["_angee"]["instance_naming"] == {
        "pattern": "${inputs.agent_name | slug | truncate(48)}",
        "max_length": 48,
    }


def test_src_workspace_writes_agent_mcp_config_verbatim() -> None:
    """The MCP document is passed through with the same safe filter as agent-default."""

    mcp_template = SRC_COPIER.parent / "template" / ".mcp.json.jinja"
    assert mcp_template.read_text(encoding="utf-8") == "{{ mcp_json | safe }}\n"
