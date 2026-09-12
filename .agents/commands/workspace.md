---
description: Create or inspect Angee workspaces.
argument-hint: "<name> [parent-ref] | status [name]"
---

# /workspace — create or inspect Angee workspaces

`/workspace` creates or inspects an Angee workspace.

- `$ARGUMENTS` as `<name> [parent-ref]` (or any create request) → create a dev
  workspace, deriving the parent ref by the skill's rules; do not silently fall
  back to `main` when the parent is unclear.
- `$ARGUMENTS` as a status/inspect request (or a bare existing name) → inspect
  Angee's native workspace and GitOps state.

Read `.agents/skills/angee-workspace/SKILL.md` and follow its **Create
Workspace** or **Inspect Workspace** workflow according to the request.
