"""Regression coverage for workspace template contracts."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC_COPIER = ROOT / "templates" / "workspaces" / "src" / "copier.yml"
SRC_CODE_WORKSPACE = ROOT / "templates" / "workspaces" / "src" / "template" / "angee.code-workspace"
SRC_AGENTS = ROOT / "templates" / "workspaces" / "src" / "template" / "AGENTS.md"

SRC_SLOTS = {"angee", "angee-messaging-bridges", "angee-arp"}
OPTIONAL_SLOTS = {"angee-messaging-bridges", "angee-arp"}
INPUT_REFERENCE = re.compile(r"\$\{inputs\.(\w+)\}")


def _workspace_inputs(manifest: dict[str, Any], **provided: str) -> dict[str, str]:
    """Return the inputs the operator resolves: `_angee.inputs` defaults, then provided values."""

    declared = manifest["_angee"]["inputs"]
    inputs = {name: str(spec["default"]) for name, spec in declared.items() if "default" in spec}
    return {**inputs, **provided}


def _resolved_modes(**provided: str) -> dict[str, str | None]:
    """Resolve each slot's `mode` the way the operator substitutes `${inputs.*}`."""

    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))
    inputs = _workspace_inputs(manifest, **provided)
    return {
        slot: None if "mode" not in record else INPUT_REFERENCE.sub(lambda m: inputs[m.group(1)], record["mode"])
        for slot, record in manifest["_angee"]["sources"].items()
    }


def _agents_text() -> str:
    """Return the workspace AGENTS.md, which renders verbatim, whitespace-normalized."""

    return " ".join(SRC_AGENTS.read_text(encoding="utf-8").split())


def test_src_workspace_materializes_one_framework_slot_and_optional_externals() -> None:
    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))
    sources = manifest["_angee"]["sources"]
    assert set(sources) == SRC_SLOTS | {"work-state"}

    for slot in SRC_SLOTS:
        record = sources[slot]
        assert record["source"] == slot
        assert record["mode"] == "${inputs.slot_mode}"
        assert record["branch"] == "${inputs.branch_prefix}/${name | slug}"
        assert record["subpath"] == slot

    assert "optional" not in sources["angee"]
    for slot in OPTIONAL_SLOTS:
        assert sources[slot]["optional"] is True
    # Every git slot takes its base from its own input so a stack whose feature
    # branch spans several repositories can pin each slot without editing the
    # rendered record; the defaults are the repositories' mainlines.
    assert sources["angee"]["ref"] == "${inputs.angee_ref}"
    assert sources["angee-messaging-bridges"]["ref"] == "${inputs.angee_messaging_bridges_ref}"
    assert sources["angee-arp"]["ref"] == "${inputs.angee_arp_ref}"

    inputs = manifest["_angee"]["inputs"]
    assert set(inputs) == {
        "agent_name",
        "mcp_json",
        "branch_prefix",
        "angee_ref",
        "angee_messaging_bridges_ref",
        "angee_arp_ref",
        "work_state_source",
        "slot_mode",
    }
    assert inputs["angee_ref"] == {
        "type": "str",
        "default": "main",
        "help": "Mainline ref for the consolidated framework source slot.",
    }
    for name in ("angee_ref", "angee_messaging_bridges_ref", "angee_arp_ref"):
        assert inputs[name]["type"] == "str"
        assert inputs[name]["default"] == "main"
        assert manifest[name] == {"type": "str", "default": "main"}

    # The branch prefix is the stack's namespace; the template keeps a neutral
    # fallback and the stack's workspace_defaults supplies its own name.
    assert inputs["branch_prefix"] == {"type": "str", "default": "workspace"}
    assert "`<branch_prefix>/<name>`" in manifest["_angee"]["description"]


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


def test_src_workspace_slot_mode_cuts_the_code_slots_and_never_the_work_state() -> None:
    """slot_mode names the three code slots' mode; the work-state slot names none.

    The default resolves every code slot to `worktree`, the mode the template
    hard-coded before the input existed, so an existing stack cuts what it cut.
    `jj` makes them jj workspaces. The work-state slot never names a mode, so the
    stack's `vcs` decides it and the workspace-wide verbs skip it (owner, Q7).
    """

    manifest = yaml.safe_load(SRC_COPIER.read_text(encoding="utf-8"))
    choices = ["worktree", "jj"]
    assert manifest["slot_mode"] == {"type": "str", "default": "worktree", "choices": choices}
    declared = manifest["_angee"]["inputs"]["slot_mode"]
    assert {key: declared[key] for key in ("type", "default", "choices")} == manifest["slot_mode"]
    assert "work-state slot names no mode" in declared["help"]

    for provided, mode in (({}, "worktree"), ({"slot_mode": "worktree"}, "worktree"), ({"slot_mode": "jj"}, "jj")):
        modes = _resolved_modes(**provided)
        assert {slot: modes[slot] for slot in SRC_SLOTS} == dict.fromkeys(SRC_SLOTS, mode)
        assert modes["work-state"] is None
    assert "slot_mode" not in str(manifest["_angee"]["sources"]["work-state"])


def test_src_workspace_agents_reads_slots_from_disk_and_ws_create_from_the_stack() -> None:
    """AGENTS.md renders verbatim: no wording follows an input.

    A workspace's slots keep the mode they were cut with, but `ws update` re-renders
    its files with the inputs of now, and fills an input the workspace never recorded
    from the stack's workspace_defaults of now. So the file tells a jj slot by its
    `.jj`, and ties `ws create` to the stack's `slot_mode`, which decides what
    `ws create` cuts, instead of rendering this workspace's input.
    """

    assert not SRC_AGENTS.with_name("AGENTS.md.jinja").exists()
    text = _agents_text()
    assert "{%" not in text and "{{" not in text

    assert "A code slot that has `.jj` is a jj workspace the operator cut and drives" in text
    assert "one without is a git worktree" in text
    assert "in the `slot_mode` the stack's `workspace_defaults` gives in `angee.yaml`" in text
    create = 'When it gives `jj`, create workspaces with `angee --root "$ANGEE_ROOT" ws create <name> --template src`.'
    assert create in text
    assert "Otherwise **do not create workspaces with `angee ws create`**; add a jj workspace by hand." in text
    # The operator manages jj slots now (angee 0.24+).
    assert "does not manage jj" not in text


def test_src_workspace_agents_carries_the_jj_agent_contract() -> None:
    """AGENTS.md states the contract for a slot that has `.jj` (proposal section 5).

    `.work` can be a jj slot under the stack's `vcs: jj` whatever slot_mode says,
    so the contract is keyed on `.jj`, never on the input.
    """

    text = _agents_text()
    for contract in (
        "## jj slots",
        "A slot that has `.jj` is a jj workspace of its source's store",
        "**One agent per workspace.**",
        "the operator's verbs then fail on the slot rather than resolve it",
        "**Write through `jj` only.**",
        "never `git commit`, `checkout`, `switch`, `reset`, `stash` or `push` there",
        "**Never rewrite commits reachable from a pushed bookmark or from the parent ref** by hand",
        "**never pass `--ignore-immutable`**",
        "The sanctioned rewrites are the rebase `ws sync-base` runs and resolving a conflict it recorded",
        "run `jj workspace update-stale`",
        "never `jj workspace add`, `forget` or `rename` it",
        '**Sync through `angee --root "$ANGEE_ROOT" ws sync-base <workspace>`.**',
        "it never merges a jj slot, and refuses `--merge`",
        "the slot reports `conflicted` with its paths",
        "`jj squash` the fix into the conflicted change, even one a pushed bookmark held before the rebase",
        "force-updated, with jj's lease, by its next publish",
        "ws source merge <workspace> <slot> <ref>`, with the ref in jj's spelling (`main@origin`)",
        "`ws source pull` refuses a jj slot",
        '**Publish through `angee --root "$ANGEE_ROOT" ws source publish <workspace> <slot>` or '
        '`angee --root "$ANGEE_ROOT" ws push <workspace>`.**',
        "Never set the bookmark or `jj git push` by hand",
        "publish refuses changes still in `@`, and commits that have conflicts or no description",
        "**Files jj will not snapshot block sync and publish.**",
        "`jj file track --include-ignored <path>`",
    ):
        assert contract in text, contract
    # The work-state slot is described once, in the slot list: no mode, so the
    # workspace-wide verbs leave it alone, and its own AGENTS.md publishes it.
    assert "It names no mode, so it is a jj workspace when the stack sets `vcs: jj`" in text
    assert "`ws sync-base` and `ws push` leave it alone either way" in text


def test_src_workspace_code_workspace_lists_every_slot() -> None:
    """The rendered code-workspace file opens exactly the new slots and work state."""

    text = SRC_CODE_WORKSPACE.read_text(encoding="utf-8")
    assert set(re.findall(r'"path": "([^"]+)"', text)) == SRC_SLOTS | {".work"}


def test_src_workspace_agents_describes_the_consolidated_framework_slot() -> None:
    text = _agents_text()

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
