---
name: push
description: Use only in the Angee repository when the user invokes /push or asks to commit and publish the current Angee workspace branch.
---

# Push

Use the `angee-workspace` skill in push mode.

`/push` means commit reviewed source changes in the current Angee workspace and
publish the workspace branch. It does not merge into the parent branch.

Load `.agents/skills/angee-workspace/SKILL.md` and follow its Push workflow,
including workspace detection, deliberate staging, generated-output exclusions,
publish/upstream handling, network escalation, and final state reporting.
