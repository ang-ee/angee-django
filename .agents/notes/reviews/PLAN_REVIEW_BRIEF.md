# Review Brief — the refactor plan + the guidelines

You are one of three independent senior reviewers (Claude subagent, Codex, Gemini).
We are about to have **Codex execute a clean rewrite** of the Angee framework core
into three packages (`compose` / `base` / `resources`). Before it runs, validate
two things: (A) the PLAN is correct, feasible, and complete, and (B) the GUIDELINES
fully and unambiguously specify the rules the executor must follow — so Codex needs
only the guidelines + the plan, no tribal knowledge.

## Read first
1. The plan: `.agents/plans/2026-05-30-compose-base-resources-refactor.md` — read it
   in full. It is the thing under review.
2. The guidelines the executor will follow: `AGENTS.md`, `docs/guidelines.md`,
   `docs/backend/guidelines.md`, `docs/stack.md`, `docs/glossary.md`.
3. The current code under `src/angee/base/` — the behavioral reference the rewrite
   must preserve. Verify the plan's claims against it where you can.

## Lens A — stress-test the PLAN

Be skeptical and concrete. Look hardest at:
- **The build/migrate split (§2.1).** Does making `angee build` emit-only and
  running `makemigrations` as a separate later process *actually* eliminate the
  `ANGEE_BUILDING`/argv flag with no correctness loss? Trace the Django app-loading
  sequence. Edge cases: first build (no prior `runtime/`), drift `--check`, a clean
  checkout, `migrate` ordering, schema SDL emission needing concrete models. Does
  emit truly need nothing from the runtime? Where exactly does `makemigrations`
  run and what loads the fresh models?
- **`base.Resource` via `source_model_modules` (§2.2).** Does an emitted abstract
  `Resource` from `angee.resources.models` correctly compose under the `base`
  label? Any duplicate-app-label or discovery pitfalls? Is `angee.resources` as a
  plain (non-addon) app sound for command discovery while contributing its model to
  `base`?
- **Dropping the manifest (§2.3).** Is the re-grounded reset/clean destructive guard
  safe (no accidental deletion)? Is emit-and-diff `--check` complete without it?
- **Imports-at-top + the Django phase-1 exception (§2.5).** Is the exception stated
  correctly and minimally? Any other unavoidable deferrals the plan misses?
- **Slice ordering & gates (§3, §4).** Can each slice actually be verified before the
  next given dependencies? Is anything un-orderable? Is any current module **lost**
  in the old→new mapping? Is the test rewrite (§5) sufficient?
- **Anything missing**: `__init__` exports, `pyproject` packaging/testpaths, the
  `angee` namespace-package rule, REBAC/migration label ripples, the example host
  settings, `angee dev` orchestration.

## Lens B — audit the GUIDELINES for executor-readiness

Could a capable engineer who knows Django but nothing about this repo execute the
plan correctly using ONLY the guidelines + the plan? For each rule the rewrite
depends on, is it stated clearly and unambiguously in the docs, or only implied?
- compose-behavior-onto-classes (and when a loose function is still allowed),
- imports-at-top + the Django app-loading exception,
- naming (modules/classes/methods), docstrings (what must have one),
- DRY / find-the-owner / prefer-deletion,
- "rewrite from guidelines, do not copy",
- the three-layer dependency rule (base imports nothing upward).
Name every gap or ambiguity and propose the **specific** sentence/edit to add to a
named doc.

## Output format — STRICT

### Verdict
1-3 sentences: is the plan ready to execute (after the guideline edits you list)?
Biggest risk.

### Plan findings
Numbered, severity-ordered (Critical/High/Medium/Low). Each: **Title**, **Location**
(plan section), **Problem**, **Fix** (concrete change to the plan).

### Guideline gaps
Numbered. Each: the rule, where it's missing/ambiguous, and the **exact** edit to
make (which doc, what sentence).

### Missing-from-plan checklist
Bullet anything the plan should cover but doesn't.

Be concrete and skeptical. Do not praise. Cite plan sections and real file paths.
