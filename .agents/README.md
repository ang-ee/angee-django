# .agents

Shared, reusable agent methodology for working on Angee — the reviewer agents,
slash commands, skills, and workflows the team runs against this repo. This
directory is committed and **public**: it documents *how* we drive agents here,
not what any single effort is doing.

Agent **work-state** — plans, notes, handover prompts, scratch — lives in
`.work/` instead. `.work/` is a gitignored symlink to a separate, private
work-state repo; it is never mirrored to the public repo.

## Layout

- `commands/` — project slash commands for non-Codex harnesses
  (`/name` resolves to `commands/name.md`). Surfaced via the `.claude/commands`
  symlink. A command may be self-contained or a thin shim that delegates to an
  owner skill in `skills/`; Codex's own slash entries come from skills instead.
- `skills/` — repo-scoped skills, scanned by Codex from the working directory up
  to the repo root. Keep shared workflow logic in one harness-neutral owner
  skill; both Codex alias skills and non-Codex command shims delegate to it.
  Small alias skills (with `agents/openai.yaml`) exist only to make Codex
  slash-list entries discoverable.
- `agents/` — subagent definitions. Surfaced via the `.claude/agents` symlink.
- `workflows/` — multi-agent workflow scripts. Surfaced via the
  `.claude/workflows` symlink.
- `tools/` — small repo-scoped scripts and helpers used by agentic workflows.

## Conventions

- One file per concern; name files in kebab-case.
- No secrets here, and nothing that names a private, unpublished repo — this
  directory is public. Anything provenance-sensitive belongs in `.work/`.
- `.claude/commands`, `.claude/agents`, and `.claude/workflows` are symlinks into
  this directory — edit the files here, not under `.claude/`.
