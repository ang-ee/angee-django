---
name: workspace
description: Use only in the Angee repository when the user invokes /workspace or asks to create or inspect Angee workspaces.
---

# Workspace

Load `.agents/skills/angee-workspace/SKILL.md`. For a create request, follow its
Create Workspace and reporting workflows; for a status request or bare existing
name, follow Inspect Workspace. A new workspace is a jj workspace of the source
store; the owner resolves the parent ref and never falls back to
`angee ws create`.
