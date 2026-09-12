# .agents

Shared, reusable agent methodology for working on Angee — the reviewer agents,
slash commands, skills, and workflows the team runs against this repo. This
directory is committed and **public**: it documents *how* we drive agents here,
not what any single effort is doing.

Every workflow and reviewer applies the root [constitution](../AGENTS.md#constitution).
Specialization changes focus, never the engineering standard; no adapter or
workflow can waive an invariant or excuse existing debt.

Private task state follows `AGENTS.md` and the **Resolve Work-State** section of
`skills/angee-workspace/SKILL.md`: use the optional workspace `work-state` slot,
which lives beside this checkout in the src template. It may be a clone or a
local-source symlink. If absent, keep task state in the conversation. Durable
conventions and pitfalls go in owning `docs/` guidelines; screenshots and logs
go in the configured gitignored scratch directories.

## Layout

- `commands/` — project slash commands for non-Codex harnesses
  (`/name` resolves to `commands/name.md`). Surfaced via the `.claude/commands`
  symlink. A command may be self-contained or a thin shim that delegates to an
  owner skill in `skills/`; Codex's own slash entries come from skills instead.
- `skills/` — repo-scoped skills. Start a repository task inside this checkout
  for ancestor-based discovery; a task started at the parent workspace may need
  to load these skills explicitly. Keep shared workflow logic in one owner skill;
  both Codex aliases and non-Codex command shims delegate to it.
  Small alias skills (with `agents/openai.yaml`) exist only to make Codex
  slash-list entries discoverable.
- `agents/` — canonical Markdown subagent definitions, surfaced via
  `.claude/agents`; generated TOML adapters are surfaced via `.codex/agents`.
- `workflows/` — multi-agent workflow scripts. Surfaced via the
  `.claude/workflows` symlink. These require the optional host runner below.
- `tools/` — small repo-scoped scripts and helpers used by agentic workflows.

## Conventions

- One file per concern; name files in kebab-case.
- No secrets here, and nothing that names a private, unpublished repo — this
  directory is public. Private task history follows the resolved work-state
  location described above.
- `.claude/commands`, `.claude/agents`, and `.claude/workflows` are symlinks into
  this directory — edit the files here, not under `.claude/`.

## Harness adapters

Reviewer `.md` files own their prompts and metadata. `.mcp.json` owns the
repository's stdio MCP server configuration. Generate the reviewer `.toml`
files and `.codex/config.toml` with `python .agents/tools/sync-adapters.py` in
the repository's Python environment; `--check` reports drift without writing.
These adapters are committed for harnesses that load them before running tools.
Do not edit generated adapters directly. The MCP adapter supports the current
`command`/`args` contract and fails for unsupported fields instead of dropping
settings. Add a deliberate projection before extending that contract.

Availability depends on the installed harness. If named reviewer discovery is
unavailable, pass the canonical Markdown prompt to an available subagent tool,
or report the lack of independent review. The `slice` skill is explicitly
opt-in and supplies this fallback; it does not replace the ordinary development
process or imply permission to commit or publish.

## Optional research runner

`workflows/research-sonnet.js` targets a host exposing
`Workflow({name: 'research-sonnet', args: '<question>'})`, Sonnet model selection,
and WebSearch/WebFetch to its agents. That host must read exported `meta`, execute
the remaining body in an async function, and provide `args`, `phase`, `log`,
`agent(prompt, options)`, `parallel(thunks)`, and `pipeline(items, ...stages)`.
`agent` resolves structured output when given a schema, text otherwise, or
`null` for a skipped invocation; `parallel` and `pipeline` return result arrays.
The runner is not bundled here, and the script is not standalone Node code.
Check that the host has this interface before invoking it. If unavailable,
perform the requested research using available tools and disclose that the
optional workflow was not run.

The script caps fetched sources and reviewed claims. Source selection is
streaming, ranked within each completed search; it is not a global top-source
ranking. Query parameters and path case remain part of source identity. Claims
need two supporting votes to be confirmed or two refuting votes to be refuted;
all other outcomes remain unverified. Votes summarize model review, not
independent evidence. Synthesis failure returns the adjudicated claims.

`tests/test_agent_methodology.py` checks adapter parity, workspace example
inputs, and helper/workflow behavior with mocked browser and runner interfaces.
See [verification commands and execution roots](../docs/checks.md).
