---
name: slice
description: Use when the user explicitly requests the slice workflow for an Angee change — diagnose, implement, independently review, verify, and hand off one coherent change.
---

# Slice

This is an optional workflow for a requested slice. Ordinary changes follow
`AGENTS.md` and `docs/guidelines.md`; loading this skill does not authorize
commits, publication, new features, or a change to the user's chosen scope.

## Workflow

1. **Diagnose and map owners.** For a bug, reproduce the failing path and name
   its cause. For a feature, establish the intended behavior and extension seam.
   Apply the architecture gate from `AGENTS.md` where relevant.
2. **Build.** Use the available delegation tool for an independent, bounded
   implementation when it helps. Supply the confirmed problem, owners to reuse,
   scope, and verification criteria. If delegation is unavailable or the change
   is small, implement it directly; no particular model or plugin is required.
3. **Review independently.** Use `.agents/agents/architecture-reviewer.md` and
   the relevant Django/React reviewer for substantive changes. Pass the canonical
   prompt through the harness's subagent tool if named reviewers are unavailable.
   Resolve verified HIGH/MED findings before calling the slice complete; explain
   dismissed findings. If no independent reviewer is available, perform a fresh
   review pass and report that independence could not be established.
4. **Verify the changed behavior.** Use `docs/checks.md` to select commands and
   their execution roots. Meaningful UI changes require browser verification;
   backend or tooling changes need evidence at their affected boundary. A
   documentation-only slice needs link/contract checks, not a live app. Report
   checks that could not run and their concrete limitation.
5. **Hand off.** Describe the behavior, evidence, unresolved findings, and
   changed files. Commit or publish only when requested or already authorized;
   honor an explicit instruction not to commit. If a different branch is needed,
   follow `.agents/skills/angee-workspace/SKILL.md`; never switch a workspace slot.
6. **Record at the owner.** Put durable conventions and pitfalls in the owning
   checked-in `docs/` guideline. Resolve optional private work-state through
   `.agents/skills/angee-workspace/SKILL.md` before writing task notes; if absent,
   keep them in the conversation. `.agents/` holds reusable methodology.

## Scope and intent

Preserve deliberate design and the user's scope. Resolve routine implementation
choices from existing owners; ask only when a material decision remains
unspecified and cannot be inferred from authorized intent. When reconstructing
a capability from another source, also apply `.agents/commands/lift.md`.
