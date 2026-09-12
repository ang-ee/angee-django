---
name: workspace
description: Use only in the Angee repository when the user invokes /workspace or asks to create or inspect Angee workspaces.
---

# Workspace

Load `.agents/skills/angee-workspace/SKILL.md`. For a create request, follow its
Create Workspace and reporting workflows; for a status request or bare existing
name, follow Inspect Workspace. The owner resolves per-slot refs and preserves
effective template defaults, including optional `work_state_source`.
