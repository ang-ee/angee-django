"""Executable contracts for repository guidance and its harness adapters.

Browser and research tests use local stubs: no browser, model, network, or
workspace lifecycle operation is invoked.
"""

from __future__ import annotations

import json
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / ".agents/tools/sync-adapters.py"


def test_reviewer_adapters_preserve_canonical_metadata_and_body() -> None:
    """Both harnesses see the same reviewer, with no orphaned generated prompts."""
    directory = ROOT / ".agents/agents"
    assert {p.stem for p in directory.glob("*.md")} == {p.stem for p in directory.glob("*.toml")}
    for source in directory.glob("*.md"):
        _, metadata, body = source.read_text().split("---", 2)
        fields = yaml.safe_load(metadata)
        adapter = tomllib.loads(source.with_suffix(".toml").read_text())
        assert adapter["name"] == fields["name"] == source.stem
        assert adapter["description"] == fields["description"]
        assert adapter["developer_instructions"].strip() == body.strip()


def test_generated_adapters_match_sources_and_mcp_semantics() -> None:
    """The committed adapters are reproducible, and both MCP hosts get one config."""
    generate = runpy.run_path(str(SYNC))["adapters"]
    for destination, content in generate().items():
        assert destination.read_text() == content, destination
    canonical = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    codex = tomllib.loads((ROOT / ".codex/config.toml").read_text())["mcp_servers"]
    assert canonical == codex


def test_adapter_generation_preserves_markdown_escaping(tmp_path: Path) -> None:
    """Quotes, literal escapes and Unicode survive the Markdown-to-TOML seam."""
    metadata = {"name": "example", "description": 'A "quoted" description — review'}
    body = 'Use """literal quotes""", `C:\\new\\thing`, and café.\n\nKeep this paragraph.\n'
    source = tmp_path / "example.md"
    source.write_text("---\n" + yaml.safe_dump(metadata) + "---\n\n" + body)
    render = runpy.run_path(str(SYNC))["reviewer_adapter"]
    actual = tomllib.loads(render(source))
    assert actual == {**metadata, "developer_instructions": body}


def test_adapter_check_detects_drift_without_overwriting(tmp_path: Path) -> None:
    """A stale adapter fails check mode and only regeneration repairs it."""
    generate = runpy.run_path(str(SYNC))["adapters"]
    for relative in (".agents/tools", ".agents/agents", ".codex"):
        (tmp_path / relative).mkdir(parents=True)
    shutil.copyfile(SYNC, tmp_path / ".agents/tools/sync-adapters.py")
    shutil.copyfile(ROOT / ".mcp.json", tmp_path / ".mcp.json")
    for destination, content in generate(tmp_path).items():
        destination.write_text(content)
    target = tmp_path / ".codex/config.toml"
    target.write_text("# stale\n")
    result = subprocess.run(
        [sys.executable, "-B", str(tmp_path / ".agents/tools/sync-adapters.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert target.read_text() == "# stale\n"


def test_workspace_create_example_uses_declared_inputs_and_keeps_stack_defaults() -> None:
    """The documented command actually selects the template ref without clearing work-state."""
    manifest = yaml.safe_load((ROOT / "templates/workspaces/src/copier.yml").read_text())["_angee"]
    skill = (ROOT / ".agents/skills/angee-workspace/SKILL.md").read_text()
    blocks = re.findall(r"```sh\n(.*?)\n```", skill, re.DOTALL)
    command = next(block for block in blocks if "ws create" in block)
    argv = shlex.split(command.replace("\\\n", " "))
    supplied = dict(argv[index + 1].split("=", 1) for index, arg in enumerate(argv) if arg == "--input")
    assert supplied.keys() <= manifest["inputs"].keys()
    ref_expression = re.fullmatch(r"\$\{inputs\.(\w+)\}", manifest["sources"]["angee"]["ref"])
    assert ref_expression is not None
    ref_input = ref_expression[1]
    assert supplied[ref_input] == "<parent-ref>"
    effective = {name: field["default"] for name, field in manifest["inputs"].items()}
    effective.update(work_state_source="team-notes")
    effective.update(supplied)
    assert effective["work_state_source"] == "team-notes"


@pytest.fixture()
def node() -> str:
    """Use the host's existing Node runtime; never install one in a source slot."""
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node is required for agent helper/runner contract tests")
    return executable


@pytest.mark.parametrize("failure", [False, True])
def test_fidelity_uses_own_e2e_dependency_and_reports_capture_failure(tmp_path: Path, node: str, failure: bool) -> None:
    """The installed harness resolves in a non-src workspace without optional examples."""
    stack = tmp_path / "stack"
    repo = stack / "workspaces/a-child/angee"
    script = repo / ".agents/tools/fidelity-capture.mjs"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / ".agents/tools/fidelity-capture.mjs", script)
    (stack / "angee.yaml").write_text("{}\n")
    dependency = repo / "packages/e2e/node_modules/@playwright/test"
    dependency.mkdir(parents=True)
    (repo / "packages/e2e/package.json").write_text('{"name":"@angee/e2e"}')
    (dependency / "package.json").write_text('{"main":"index.cjs"}')
    (dependency / "index.cjs").write_text(
        "const fs = require('node:fs');\n"
        "const page = {\n"
        f"  async goto() {{ if ({str(failure).lower()}) throw new Error('fixture capture failed'); }},\n"
        "  async evaluate() { return {headings: [], summary: {}}; },\n"
        "  async screenshot(options) { fs.writeFileSync(options.path, JSON.stringify(options)); },\n"
        "};\n"
        "exports.chromium = {async launch() { return {\n"
        "  async newContext() { return {async newPage() { return page; }}; },\n"
        "  async close() { console.log('fixture browser closed'); },\n"
        "}; }};\n"
    )
    result = subprocess.run(
        [node, str(script), "http://fixture.invalid", "/notes", "capture"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == int(failure), result.stderr
    assert "fixture browser closed" in result.stdout
    output = stack / "test-results/fidelity/capture.json"
    assert output.exists() is not failure
    if not failure:
        assert json.loads(output.read_text())["url"] == "http://fixture.invalid/notes"
        screenshot = json.loads(output.with_suffix(".png").read_text())
        assert screenshot["fullPage"] is False


RESEARCH_RUNNER = r"""
import { readFileSync } from 'node:fs';
const [filename, serialized] = process.argv.slice(2);
const config = JSON.parse(serialized);
const source = readFileSync(filename, 'utf8').replace('export const meta', 'const meta');
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
const fetched = [];
const agent = async (prompt, options) => {
  if (options.label === 'scope') return {
    question: 'Fixture question',
    angles: config.searches.map((_, index) => ({label: String(index), query: 'fixture'})),
  };
  if (options.phase === 'Search') {
    const results = config.searches[Number(options.label.split(':')[1])];
    if (results === 'error') throw new Error('fixture search failed');
    return results === null ? null : {results: results.map(url => ({url, title: url, relevance: 'high'}))};
  }
  if (options.phase === 'Fetch') {
    const url = prompt.match(/\*\*URL:\*\* (.*)\n/)[1];
    fetched.push(url);
    return {sourceQuality: 'primary', claims: [{claim: url, quote: 'fixture evidence', importance: 'central'}]};
  }
  if (options.phase === 'Verify') {
    const verdict = config.votes[Number(options.label[1])];
    if (verdict === 'error') throw new Error('fixture verification failed');
    return verdict === null ? null : {verdict, evidence: 'fixture evidence', confidence: 'high'};
  }
  if (options.label === 'synthesize') throw new Error('fixture synthesis unavailable');
  throw new Error('Unexpected fixture agent: ' + options.label);
};
const parallel = tasks => Promise.all(tasks.map(task => task()));
const pipeline = (items, ...stages) => Promise.all(items.map(async item => {
  for (const stage of stages) item = await stage(item);
  return item;
}));
const outcome = await new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', source)(
  'Fixture question', agent, parallel, pipeline, () => {}, () => {},
);
process.stdout.write(JSON.stringify({fetched, outcome}));
"""


def run_research(node: str, searches: list[list[str] | str | None], votes: list[str | None]) -> dict[str, Any]:
    """Execute the real workflow body against the documented host protocol stub."""
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-",
            str(ROOT / ".agents/workflows/research-sonnet.js"),
            json.dumps({"searches": searches, "votes": votes}),
        ],
        input=RESEARCH_RUNNER,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_research_fetch_budget_is_hard_even_for_high_relevance(node: str) -> None:
    searches = [[f"https://fixture.invalid/{angle}/{item}" for item in range(6)] for angle in range(6)]
    result = run_research(node, searches, ["supported", "supported", "supported"])
    assert len(result["fetched"]) == 15
    assert len(result["outcome"]["confirmed"]) == 15
    assert result["outcome"]["stats"]["budgetDropped"] == 21
    assert result["outcome"]["report"] is None


def test_research_keeps_distinct_query_and_case_sources_and_handles_missing_searches(node: str) -> None:
    urls = [
        "https://fixture.invalid/Article?id=1",
        "https://fixture.invalid/Article?id=2",
        "https://fixture.invalid/article?id=1",
        "https://fixture.invalid/Article?id=1#section",
    ]
    result = run_research(node, [None, "error", urls], ["supported", "supported", "supported"])
    assert result["fetched"] == urls[:3]


@pytest.mark.parametrize(
    ("votes", "category"),
    [
        (["supported", "supported", "refuted"], "confirmed"),
        (["refuted", "refuted", None], "refuted"),
        (["supported", "refuted", None], "unverified"),
        ([None, None, None], "unverified"),
        (["error", "error", "unverified"], "unverified"),
    ],
)
def test_research_distinguishes_support_refutation_and_abstention(
    node: str, votes: list[str | None], category: str
) -> None:
    result = run_research(node, [["https://fixture.invalid/claim"]], votes)["outcome"]
    assert len(result[category]) == 1
    for other in {"confirmed", "refuted", "unverified"} - {category}:
        assert not result.get(other)
