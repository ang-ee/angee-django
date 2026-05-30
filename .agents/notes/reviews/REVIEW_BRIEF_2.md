# Review Brief 2 — class composition, imports, lifted code, manifest (`src/angee/base/`)

You are one of three independent senior reviewers (Claude subagent, Codex, Gemini).
The framework core was just refactored (file split, ownership consolidation, ceremony
removal). Review the CURRENT state of the code, not its history.

## STEP 1 — Read the docs (the standard you judge against)

Read fully before reviewing. They were just updated — honor the new rules:
1. `docs/backend/guidelines.md` — especially the new rules: **compose behavior onto
   the class that owns the data** ("a dataclass that only holds fields while a sibling
   module mutates and emits from it is a missing class"), **imports go at the top of
   the module** (a function-local/deferred import is an architecture smell), the
   Django-Native Rule, and Naming.
2. `AGENTS.md` — the Constitution ("Find the owner", "prefer deletion to abstraction",
   "less is more", compose at build time).
3. `docs/glossary.md`, `docs/stack.md` — vocabulary and library ownership.

## STEP 2 — Scope

Every `.py` file under `src/angee/base/`. Reference `examples/notes-angee/` and
`tests/` for intent. These four lenses are the FOCUS of this pass (judge the rest
against the Django/constitution baseline only where it rises to a real finding):

### Lens A — Behavior that should be composed onto a class (PRIMARY)
Find every place where loose module-level functions operate on the same object and
should be methods on a class that owns that data. The known anchor:
`compose/emission.py` + `compose/pipeline.py` hold a passive `RuntimePlan` dataclass
plus ~12 module functions (`plan_runtime`, `check_runtime`, `emit_runtime_sources`,
`import_runtime_models`, `normalize_migration_headers`, `emit_schema_sdl`,
`check_schema_sdl`, `reset_runtime_dir`, `run`, `clean_runtime`, …) that all take or
build that plan. The intended shape is a single cohesive runtime build class — e.g.
an `AngeeRuntime` object with methods for plan/emit/check/reset/import/normalize/sdl —
not a dataclass surrounded by functions. Map what such a class should own, and find
any OTHER dataclass-plus-sibling-functions patterns in the tree (resources, graphql).

### Lens B — Non-top-level imports (PRIMARY)
Find EVERY function-local / deferred / inside-method import under `src/angee/base/`
(e.g. `apps.py`, `resources/managers.py`, `resources/models.py`, `mixins.py`,
`signals.py`, `models.py`'s bottom re-export). For each: state why it is deferred
(import cycle? Django app-loading order? optional dep?), and whether it is fixable by
moving to module top / fixing the seam, or signals a deeper cycle to restructure. The
`models.py` ↔ `resources/models.py` re-export cycle is a prime suspect — say how to
break it cleanly.

### Lens C — Lifted / unearned code (PRIMARY)
The framework was assembled partly by lifting code; some may not earn its keep. Flag
speculative generality, over-abstraction, dead defensiveness, options/params nothing
uses, and anything "prefer deletion to abstraction" would cut. Be concrete about what
to delete.

### Lens D — Remove `.angee-manifest.json` (DECIDED — assess impact)
The team has decided to drop the generated `.angee-manifest.json`. The check does NOT
need git or a manifest marker: `--check` renders the would-be output to in-memory
strings and compares them against what is currently on disk before any overwrite —
which is essentially what `_check_runtime` already does (emit to a temp tree, diff
files). Map EVERYTHING that writes, reads, or depends on the manifest:
- `emission.emit_runtime_sources` writes `.angee-manifest.json` (addons / resources /
  runtime_apps).
- `reset_runtime_dir` and `clean_runtime` use the manifest's existence as the "is this
  an Angee runtime directory" safety guard before deleting.
- `_is_checked_runtime_source` / `_generated_source_files` include it in drift checks.
Specify exactly what removing it requires: how the reset/clean safety guard should be
re-grounded WITHOUT the manifest (the host already passes an explicit, known
`ANGEE_RUNTIME_DIR` — is a content marker even needed?), and confirm `--check` stays
correct by emit-to-string-and-diff alone.

## Output format — STRICT

### Summary
3-6 sentences: biggest remaining structural issue through these four lenses.

### Findings
Numbered, ordered by severity (Critical/High/Medium/Low). Each:
- **Title**
- **Lens**: A / B / C / D (or Other)
- **Location**: `path:line`
- **Severity**
- **Problem**: what's wrong, citing the doc rule
- **Recommendation**: concrete fix (the class/method shape, the import move, the deletion)

### Proposed `AngeeRuntime` shape
A short sketch: what class(es) should own the composition behavior, which methods,
and what becomes of `RuntimePlan`, `pipeline.run`, and the emission module functions.

### Inline-import inventory
A table/list of every non-top-level import found, with the reason and the fix.

### Top recommendations
Ranked, one sentence each.

Be concrete and skeptical. Do not praise. Cite real paths/lines.
