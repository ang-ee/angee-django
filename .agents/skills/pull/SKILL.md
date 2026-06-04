---
name: pull
description: Use only in the Angee repository when the user invokes /pull or asks to bring parent, workspace, or branch changes into the current Angee workspace branch.
---

# Pull

Use the `angee-workspace` skill in pull mode.

`/pull` means bring changes into the current Angee workspace branch. With no
argument, pull from the workspace parent ref or parent workspace. With an
argument, merge that workspace or branch/ref into the current workspace.

Load `.agents/skills/angee-workspace/SKILL.md` and follow its Pull workflow,
including source worktree checks, clean/pushed requirements, explicit override
rules, merge conflict handling, and final state reporting.
