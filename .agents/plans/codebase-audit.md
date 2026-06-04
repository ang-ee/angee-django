# Codebase audit & decomposition cleanup

Systematic, addon-by-addon review of the whole tree against our own guidelines,
fixing decomposition / naming / level drift until every unit is "clean." The
reviewers encode no rules of their own — they judge against `AGENTS.md`,
`docs/guidelines.md`, `docs/backend/guidelines.md`, `docs/frontend/guidelines.md`,
`docs/stack.md`. This plan is the source of truth and the coverage ledger.

## Goal / non-goal

- **Goal:** owner-correct decomposition everywhere — behavior on the model /
  manager / queryset / cohesive class that owns the data; consistent file,
  class, and method naming across addons; no logic at the wrong level; no
  guideline drift. Each fix lands as one gated, reviewed commit per unit.
- **Non-goal:** behavior change, new features, or touching generated `runtime/`.
  This is a structure/clarity pass. If a fix needs a behavior decision, stop and
  surface it — don't smuggle it into a refactor.

## Review units (coverage matrix)

One row per **unit** = (addon|package). Fix order = lowest layer first, so
cleaned base patterns propagate downward before consumers are touched. Audit is
read-only and may run in any order. `runtime/` is output — never reviewed.

| # | Unit | Side | Status: scan / audit / fix / verify |
|---|------|------|--------|
| 1 | `src/angee/base` | django | ☑ / ☑ / ☐ / ☐ |  2 med (base-002 deletion.py loose builders → DeletionPreviewNode factories) · 1 low |
| 2 | `src/angee/compose` | django | ☑ / ☑ / ☐ / ☐ |  audit-clean (3 low; compose-001 comment fix) |
| 3 | `src/angee/resources` | django | ☑ / ☑ / ☐ / ☐ |  2 high (resources-001 base→resources private import) · 2 med · 2 low |
| 4 | `src/angee/iam` | django | ☑ / ☑ / ☐ / ☐ |  2 high (iam-001/002 scattered status+OIDC identity → managers, collapse identity.py) · 3 med · 2 low |
| 5 | `src/angee/integrate` | django | ☑ / ☑ / ☐ / ☐ |  near-clean: 2 med (integrate-001 type deliver_event kind vs EventKind) · 2 low |
| 6 | `src/angee/operator` | django **+ react** (hybrid) | ☑ / ☑ / ☐ / ☐ |  1 high (operator-001 daemon.py unmarked graphql import) · 2 med (dead roles.ts/fixtures.ts) · 2 low |
| 7 | `examples/notes-angee/src` (host + example/notes) | django | ☑ / ☑ / ☐ / ☐ |  clean exemplar except 1 high (notes-001 cross-seam private reaches: RebacQuerySet._apply_scope_in_place, Note._public_id_lookup) · 2 med · 1 low |
| 8 | `packages/sdk` | react | ☑ / ☑ / ☐ / ☐ |  2 high (sdk-001 dup useStableVariables; GraphQL name re-derivation vs SDL owner) · 4 med · 3 low |
| 9 | `packages/base` | react | ☑ / ☑ / ☐ / ☐ |  5 high (pkgbase-001 product lifecycle/field leak in framework binding; views/ missing DataView/Filter owner; ListView/GroupListView dup) · 4 med · 2 low |
| 10 | `packages/storybook` | react | ☑ / ☑ / ☐ / ☐ |  2 high (storybook-001 story hand-rolls ListView vs rendering real component; stale storySort taxonomy) · 2 med |
| 11 | `examples/notes-angee/src/web` | react | ☑ / ☑ / ☐ / ☐ |  thin/clean: 1 med (web-001 host re-implements PassthroughChrome → export from @angee/base) · 1 low |
| 12 | `packages/e2e` | react | ☑ / ☑ / ☐ / ☐ |  1 high (e2e-001 reference POM imports expect from @playwright/test — dual-instance trap) · 1 med · 1 low |

## Red-flag checklist (what the audit hunts, with its owning rule)

Every finding cites the guideline it violates. The hunt list:

**Decomposition / find-the-owner**
- Scattered functions outside classes — `def _x(thing, ...)` over a passive
  model/dataclass → must be a model/manager/queryset/cohesive-class method
  (`docs/backend/guidelines.md` "Domain behavior lives on models, managers, and
  querysets"; "a module of loose functions wrapped around a passive dataclass …
  is a missing class").
- Wrong primitive — a `@dataclass`/plain class/"service" that should be a model,
  queryset, or manager; a dataclass that only holds fields while a sibling
  mutates it.
- Wrong level — a consumer addon solving what the framework owns (or vice
  versa); build-time logic at runtime or runtime logic in the composer
  (`AGENTS.md` Repository Role; Package Layering).
- Non-abstract source model — any `abstract = False` authored in `src/` (the
  composer emits concrete; see [[source-models-always-abstract]]).
- Type-switch / name-list heuristic — `isinstance` chains, `__name__ in {…}`,
  type dispatch from outside → wants polymorphism or a model-owned declaration
  ("a function that switches on a value's type wants polymorphism").
- Forwarding wrapper — a function/class that only forwards/normalizes/renames a
  Django object → delete it ("A wrapper must prove it adds a real new concept").

**Imports & placement**
- Function-local / deferred imports outside a marked phase-1 AppConfig deferral
  or `TYPE_CHECKING` ("Imports go at the top of the module").

**Naming (structural — a wrong name is a broken contract)**
- Module not single-word / role-named (`models.py`, `managers.py`, …).
- Class without its role suffix (`*Manager`, `*QuerySet`, `*Mixin`, `*Config`).
- Method not verb-first from the vocabulary (`get_*`/`is_*`/`as_*`/`to_*`/
  `from_*`/`create_*`); camelCase in Python; constants not `UPPER_SNAKE`.
- Cross-addon inconsistency — same concept named/structured two ways across
  addons (the consistency axis; caught in Phase 4).

**DRY**
- Same rule in two places; same shape in three places; same words in docs not
  kept at the owner.

**Frontend (react units)**
- `any` / `as unknown as` / `Record<string, unknown>`; loose utils that should be
  hooks/components; hooks-rules / effect / type-narrowing issues (react-reviewer
  lenses); files off the naming convention.

**Hygiene**
- Dead/unused code; lifted/unearned code; docs↔code drift (a mismatch is a bug).

## Findings schema

One ledger per unit at `.agents/audit/findings/<unit-slug>.md`, a list of:

```
- id: <unit>-NNN
  loc: <path>:<line>
  category: <one red-flag key above>
  severity: critical | high | medium | low
  rule: <doc citation>
  finding: <what's wrong, 1 line>
  fix: <the owner-correct change, 1 line>
  status: open | fixing | verified | wontfix(<reason>)
```

`wontfix` is allowed only with a cited reason (e.g. a scanner false-positive: a
frozen cache dataclass, a legit module-level pure renderer, a phase-1 deferral).

## Phases

- **Phase 0 — Mechanical scan** (deterministic, per unit):
  `python3 .agents/audit/scan.py <unit-path>` → candidate hotspots. Seeds the
  reviewers; not authoritative.
- **Phase 1 — Audit** (read-only, parallel reviewers): per unit, run the lenses
  against the docs + the Phase-0 hotspots → write the findings ledger.
  - django unit → `architecture-reviewer` (lead: decomposition/owner-map/naming/
    DRY/level) **+** `django-reviewer` (runtime correctness sanity on any move).
  - react unit → `architecture-reviewer` **+** `react-reviewer`.
- **Phase 2 — Synthesis & ordering** (checkpoint): collate all ledgers; cluster
  cross-cutting findings into shared-owner fixes (fix the owner once, not N
  copies); produce the dependency-ordered fix queue. **Present to the human;
  get approval before any mutation.**
- **Phase 3 — Fix loop** (one unit at a time, fix order): triage → hand the
  unit's `open` findings to **Codex** to *reconstruct* the affected modules from
  their contract + tests + guidelines (not mechanically port) → run the full
  gate → re-audit the unit (loop-until-dry: re-run lenses; if new `open`
  findings, fix again; stop after a clean pass) → commit one unit. Mark `verify`.
- **Phase 4 — Final consistency & docs reconciliation:** whole-tree
  `architecture-reviewer` pass for cross-addon naming/structure consistency; fix
  any docs↔code drift the audit surfaced; if the audit clarified a pattern,
  update the guideline at its owner.

## Per-unit pipeline (the loop body)

For unit U, in fix order, each loop iteration advances U by the next incomplete step:

1. **scan** — run the scanner → ledger seeded.
2. **audit** — spawn the two lenses (parallel) → fill the ledger (`open`).
3. **fix** — Codex reconstructs the flagged modules; verify scoped to `ruff` +
   `mypy` inside Codex (the Codex sandbox hangs on `manage.py`/`pytest`).
4. **gate** — *I* run the full gate from repo root:
   `angee build → makemigrations --check → migrate → rebac sync → resources load
   → schema --check → pytest → ruff → mypy` (react units: `pnpm typecheck →
   vitest → e2e`). Drift/red = back to step 3.
5. **verify** — re-run the two lenses on U; every finding must be `verified` or
   `wontfix(reason)`, zero regressions.
6. **commit** — one commit `audit(<unit>): <decomposition summary>`; tick the
   matrix; advance to the next unit.

## How to run the loop

Recommended: do this in a dedicated workspace so the refactor commits are
isolated and reviewable —
`angee ws create audit --template dev --input base_ref=<this branch>`.

**Step 1 — Audit sweep (read-only, safe to run fast).** Run:

> `/loop audit the codebase per .agents/plans/codebase-audit.md: pick the next unit whose audit box is unticked, run scan.py + the two review lenses against the guidelines, write its findings ledger, tick the audit box, and stop when every unit is audited`

It self-paces one unit per iteration; nothing is mutated. When the matrix's audit
column is full it produces the **Phase 2 drift map** and stops for your review.

**Step 2 — Approve the fix queue.** Read the synthesized drift map; approve or
re-order. (Hard gate — no code is changed until you say go.)

**Step 3 — Fix loop (mutating, gated).** Run:

> `/loop fix the next unclean unit per .agents/plans/codebase-audit.md: triage its open findings, dispatch a Codex reconstruction, when Codex is done run the full gate, re-audit until dry, commit the unit, tick the matrix, and move to the next unit in fix order`

It babysits Codex async (kick off → check done → gate → verify → commit →
advance), one unit per pass, until the whole matrix is `verify`-ticked. Then it
runs Phase 4 and stops.

## Phase 2 — drift map (audit complete; AWAITING APPROVAL before any fix)

**58 findings: 16 high · 22 medium · 20 low.** Per-unit ledgers in
`.agents/audit/findings/*.md`. compose is clean; integrate/web/notes near-clean;
`packages/base` carries the most (5 high). Findings cluster into 6 themes — fix
the **owner** once and the copies collapse:

- **A · scattered functions → the owning class** (the dominant theme):
  iam-001 (AccountStatus rank/decode → enum + managers), iam-002 (`identity.py`
  module → `UserManager`/account managers, collapse the file), base-002
  (`deletion.py` builders → `DeletionPreviewNode` factories), pkgbase-002
  (`DataViewState` 417-line free-fn module → typed `DataView`), pkgbase-004
  (filter-shape free fns → typed `Filter`).
- **B · wrong-level / product leak in framework:** pkgbase-001 (the styled
  binding hardcodes the notes app's `DRAFT/IN_REVIEW/…` lifecycle + `status`/
  `title` field names — every consumer inherits a foreign enum), resources-002
  (resource-tier vocabulary declared 3×).
- **C · cross-seam private reaches → add a public owner verb:** resources-001
  (`base.discovery._addon_aliases`), notes-001 (`RebacQuerySet._apply_scope_in_place`,
  `Note._public_id_lookup`).
- **D · framework-private behavior consumers must re-implement → export the owner:**
  e2e-001 (re-export `expect` from `@angee/e2e`), web-001 (export
  `PassthroughChrome` from `@angee/base`), storybook-001 (render the real
  `ListView`, don't hand-roll it).
- **E · DRY duplication:** pkgbase-005 (`titleCase` ×4, already drifted),
  pkgbase-003 (`ListView`/`GroupListView` near-identical), sdk-001
  (`useStableVariables` dup) + sdk value-memo ×3 + GraphQL names re-derived vs SDL.
- **F · dead / unearned / speculative:** sdk-002 (unreachable `node.groups`),
  operator dead `roles.ts`/`fixtures.ts`, integrate-001 (`kind: Any` + unused
  `EventKind`). **G · imports:** operator-001 (hoist hard-dep `graphql`).
  **H · docs/naming drift:** storybook-002 (stale `storySort`), `iam_oauth_clients.py`.

### Fix order (owner-first, lowest layer first)

Backend: **base → compose → resources → iam → integrate → operator → notes**
Frontend: **sdk → packages/base → storybook → web → e2e**
(base exposes the public verbs notes-001/resources-001 need; sdk is the owner
`@angee/base` builds on; web/storybook depend on a cleaned `@angee/base`.)

### Three need a DECISION, not just a refactor (flag before Codex)

1. **pkgbase-001** — where do a consumer's lifecycle vocabulary + field names come
   from once the framework stops hardcoding notes' values? (addon-provided view
   config? a typed prop?) This is a small API design choice.
2. **resources-002** — the tier vocabulary is duplicated partly because
   **base must not import resources** (layering). Real fix is likely "move the
   tier owner into base," not "delete base's copy." Confirm the owner.
3. **iam-002** — collapsing `identity.py` onto managers is a large, behavior-
   adjacent move (it's the OIDC login path just hardened). Confirm scope / that
   it stays a pure structural move.

## Guardrails

- Read-only audit and mutating fix are **separate loops** with a human checkpoint
  between — never auto-refactor the tree unattended.
- One unit = one commit = one revert unit. Gate must be green before commit.
- Codex *reconstructs from contract*, never pastes/ports; it doesn't keep old
  modules importable (`docs/backend/guidelines.md`).
- A refactor that wants a behavior change is out of scope — stop and surface it.
- `wontfix` needs a cited reason; the scanner over-flags `isinstance`/dataclass
  on purpose (recall is better than precision) — the reviewer prunes.
- Workspace is pinned; never `git checkout` inside it — branch via a new
  workspace for parallel work.
```
