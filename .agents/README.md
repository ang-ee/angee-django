# .agents

Working area for agentic development: durable notes, plans, handover prompts,
slash commands, subagent definitions, and project bookkeeping.

Prefer this directory over scratch locations like `/tmp`. Anything worth keeping
between sessions lives here and is committed; only genuinely throwaway scratch
(and anything carrying sensitive content you do not want in the repo) stays out.

## Layout

- `commands/` — project slash commands for non-Codex harnesses
  (`/name` resolves to `commands/name.md`). Surfaced via the
  `.claude/commands` symlink; Codex app slash entries come from skills instead.
- `skills/` — repo-scoped Codex skills. Codex scans `.agents/skills` from the
  current working directory up to the repo root. Keep shared workflow logic in
  one owner skill; small alias skills may exist only to make slash-list entries
  discoverable.
- `agents/` — subagent definitions. Surfaced via the `.claude/agents` symlink.
- `plans/` — implementation plans, one file per effort.
- `notes/` — durable memory and working notes meant to outlive a single session.
- `handovers/` — handover prompts that pass context to the next session or to a
  spawned agent.
- `pm/` — project-management bookkeeping; `pm/templates/` holds reusable
  templates.

## Conventions

- One file per concern; name files in kebab-case.
- No secrets here.
- `.claude/commands` and `.claude/agents` are symlinks into this directory — edit
  the files here, not under `.claude/`.
