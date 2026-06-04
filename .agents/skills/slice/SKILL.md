---
name: slice
description: Use when landing a code change in this repo — fixing a bug, building a feature, or lifting a capability. Land it as one disciplined slice — diagnose to root cause, delegate substantive builds to codex, gate with adversarial review, prove it by rendering the live app (not just green tests), and defer decisions that are the human's.
---

# Slice

Land ONE change as a disciplined slice. The discipline is the point: never skip
a stage, even for a one-line fix — uniform process is what keeps quality steady
across a long or unattended run.

## The pipeline

Run every slice through these stages, in order.

1. **Diagnose to a root cause first.** Reproduce it, read the failing path, and
   name the exact cause before touching code. A precise diagnosis becomes a tight
   build brief; a vague one wastes a build round. Most of the real work is here,
   not in the coding. Narrow until you can state the cause in one sentence.
2. **Build.** Delegate substantive builds and lifts to **codex**
   (`codex:codex-rescue` via the Agent tool, foreground `--wait`). Give it a brief
   that states: the problem, the *confirmed* root cause, the constraints, what to
   reuse, and how to verify. Do only small, fully-understood folds inline.
3. **Review — adversarially.** Run the independent reviewers on the diff:
   `architecture-reviewer` always; `django-reviewer` for backend and
   `react-reviewer` for frontend. They are a gate, not a formality. They catch the
   half-applied fix, the idiom that works live but fails under test settings, the
   third divergent copy you didn't see.
4. **Fold** the findings worth folding (HIGH/MED, plus cheap doc/code-drift LOWs).
   Prefer reusing the owner over patching your copy.
5. **Verify by RENDERING.** Drive the real app and watch it behave — do NOT trust
   green typecheck/tests alone. Tests pass on code that crashes the live page
   (a REBAC-guarded relation pulled out of actor scope; a 200 that never settles;
   a blank form). Then run the relevant suite too. The screen is the truth.
6. **Commit** small and single-concern, with **zero provenance** — never mention a
   prototype, a plan, "lifted/ported from", or an earlier version in code,
   comments, or the message. On the default branch, branch first.
7. **Record** any non-obvious trap as a durable note (a memory and/or `.agents/`
   note) so it is never re-investigated or re-hit.

## Non-negotiables — what makes this work

- **Prove it by rendering.** The single highest-leverage habit. If you can't
  watch it work, you haven't verified it. Tests alone repeatedly miss live breaks.
- **Adversarial review is a gate.** Independent skeptics catch what you and the
  tests miss; treat their HIGH/MED findings as blocking until resolved.
- **Diagnose before building.** Hand codex a cause, not a symptom.
- **Find the owner.** Reuse the one place that owns a fact or behavior; don't add
  a divergent copy. Defer to the stack (`docs/stack.md`) and existing primitives.

## The boundary — know what is yours to decide

Fix freely anything with **no flagged intent and a clear right answer**. **Defer to
the human** anything that is:

- flagged in the code as a deliberate choice (a "pending …" / "deliberately …"
  comment, a defended docstring),
- a brand or visual-design decision, or
- feature scope.

Surface deferred items clearly and record them; do not override documented intent,
and do not manufacture work to look busy. Stopping at the line where a change
becomes the human's call is part of the method, not a failure of it.
