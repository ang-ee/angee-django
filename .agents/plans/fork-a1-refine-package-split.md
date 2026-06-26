# A1 — Finalize the Refine package split (execution plan)

> Status: PLAN ONLY. Scope: fork A1 = finish the physical Refine package split; folds Q6 (fill the empty `@angee/ui` + `@angee/app` stubs) and Q7 (write the frontend layering doc). L-effort, High-risk: touches every addon web package and the example host.
>
> Plan of record this finalizes: `.agents/plans/refine-greenfield-rebuild-plan.md` Phases 3-6. Working ledger: `.agents/plans/refine-revolution-pass-ledger.md`.

## 0. Starting state (research findings)

The split is roughly half done. The dialect/metadata layers (refine, resources) are populated; the rendered + composition layers (ui, app) are empty stubs; the old shells (base, data, sdk) still own the bulk and are still consumed by every addon and the host.

| Package | Files | State |
|---|---|---|
| `packages/base/src` | **310** | OLD shell, load-bearing. `ui/`(56), `views/`(75), `widgets/`(43), `chrome/`(25), `layouts/`(15), `lib/`(14), `feedback/`(9), `page/`(9), `auth/`(8), `preview/`(6), `communication/`(6), `fragments/`(36), `toolbars/`(2), `i18n/`(2) + `createApp.tsx`, `testing.tsx`, `index.ts` |
| `packages/data/src` | **10** | OLD shell. `auth.tsx`, `authored-hooks.tsx`, `hooks.tsx` (aggregate/action/deletePreview/facets/groupBy), `revisions.ts`, `i18n.ts`, `errors.ts` |
| `packages/sdk/src` | **9** | OLD shell. `define-addon.ts`, `runtime.ts`, `make-context.ts`, `i18n.ts` |
| `packages/refine/src` | **14** | NEW, populated. provider/transport/live, router, operations, typed-document, filter-codec, query-invalidation, stable-deps |
| `packages/resources/src` | **12** | NEW, populated. metadata, resources, resource-types, rows, access-control, invalidation |
| `packages/ui/src` | **1** | EMPTY STUB (`export {};`) |
| `packages/app/src` | **1** | EMPTY STUB (`export {};`) |
| `packages/storybook/src` | 86 | Consumer; deps on base+data+sdk+refine+resources |

Couplings (the migration surface): `packages/base` imports `@angee/sdk` in 21 files, `@angee/data` in 13, `@angee/refine` in 22, `@angee/resources` in 40. `createApp.tsx` imports data/resources/refine/sdk. The example host + every addon web still depend on `@angee/base`+`@angee/sdk`.

**Ledger verdict (risk framing):** `refine-revolution-pass-ledger.md:978-980` — the rebuild was genuinely done; the remaining work is two targeted cleanups, *not* this physical split (which it calls "cosmetic by comparison"). This split moves code between packages that already pass typecheck/test/build → **mechanical relocation, not behavior change.** Treat any behavior change found mid-slice as out of scope (note, don't fix inline).

**Two real debts to NOT entrench** (`ledger:959-976`): the `local-rows.ts` hand-rolled `_bool_exp` evaluator (HIGH) and the parallel i18n path (MEDIUM). **Recommendation: relocate both as-is**, carry as named debt in the Q7 doc; fixing them inside the move breaks the green-at-every-step guarantee.

**Cutover-cache hazard** (the previous attempt's killer): exactly one `<Refine>` root + one react-query cache + one live/urql engine today (`createApp.tsx:267-280`; `ledger:935-950`). The split must preserve this single-engine invariant — relocate the files that build the one provider/cache; never instantiate a second while the old one still runs.

## Phase 0 (Q7) — The frontend layering doc is the FIRST deliverable

Nothing else starts until the contract is written. Add a "Package layering" section to `docs/frontend/guidelines.md` (today only prose at lines 63-68):

1. **Target DAG** (from `refine-greenfield-rebuild-plan.md:388-410`): rented libs → `@angee/refine` (Hasura-dialect binding, zero domain/metadata) → `@angee/resources` (metadata→Refine bridge) → `@angee/ui` (rendered binding + headless view-state) → `@angee/app` (composition + shell, the only package depending on all above) → `@angee/<domain>` addons.
2. **Current→target table** (from plan `:267-276`, `:681-709`): ui primitives/views/chrome/widgets→`@angee/ui`; `createApp`/`defineBaseAddon`→`@angee/app`; `defineAddon`/runtime/make-context→`@angee/app`; data hooks→`@angee/refine` dialect; auth→`@angee/app`; i18n→`@angee/app` (collapse the parallel path).
3. **One-way rules:** `@angee/refine` imports only rented libs (no resources/ui/app, no metadata). `@angee/resources` must NOT import `@angee/refine`. `@angee/ui` may import refine+resources, not app. **`@angee/app` is the ONLY package that may import compose/createApp-level concerns**; `@angee/ui` consumes composition contracts via React context whose Provider `@angee/app` mounts. After the split, **no addon imports `@angee/base`/`@angee/data`/`@angee/sdk`.**
4. **Two named debts** carried as-is (local-rows, parallel i18n) with a "fix tracked separately" note.

**Gate:** doc review only. **STOP for architect confirmation** — the layering rules, the `@angee/data` hooks destination, and auth/i18n placement are architect calls every later wave depends on.

## Waves / slices

Principle: **build the new package fully and green BEFORE flipping any consumer; flip consumers package-by-package; delete the old shell only after its last importer is gone.** Each slice: Owner · Move (with search-seed = the grep that finds it and later proves deletion) · Gate · Sequence.

### Wave A — Fill `@angee/ui` (~280 of base's 310 files)

Sub-slices, render-DAG order (each green before the next imports it):
- **A1 primitives** — `base/src/ui/*` (56) → `packages/ui/src/primitives/`. Gate: `pnpm --filter @angee/ui typecheck && vitest run`. *Architect decision:* `lib/` (14, cn/tv/tones/dnd) → `@angee/ui` (render styling) — recommended.
- **A2 widgets + feedback** (43+9) — after A1.
- **A3 views + headless view-state** (75) — `ListView`/`FormView`/`RecordView`/`ResourceList`/`RowsListView` + modes + visualizations + `resource-view-*` + `local-rows.ts` (debt as-is). *Architect decision:* relocate-then-rename to the canonical `view-state.ts` etc. (recommended: relocate as-is, rename follow-up). Gate: the heavy vitest set.
- **A4 chrome+layouts+page+fragments+preview+toolbars+communication** — after A1-A3.

After Wave A: `@angee/base` retains only `createApp.tsx`, `i18n/`, `auth/`, `testing.tsx`, `index.ts`. Do NOT delete base yet.

### Wave B — Fill `@angee/app`

- **B1 composition contracts** — `@angee/sdk/src/{define-addon,runtime,make-context}.ts` → `packages/app/src/`. *Architect decision:* `useSlot`/`useWidget`/`AppRuntime` context in `@angee/app`, `@angee/ui` reads via context (plan default: yes).
- **B2 createApp + shell** — `base/src/createApp.tsx` (+ `defineBaseAddon`, i18n provider) → `packages/app/src/create-app.tsx`. **The single-engine owner — cache-hazard epicenter; moves once, intact.**

### Wave C — Settle `@angee/data` (murkiest; most architect input)

- `hooks.tsx`/`revisions.ts`/`authored-hooks.tsx` → `@angee/refine` dialect hooks. **DAG-edge issue:** they read `resourceOperationTarget` from `@angee/resources`, but `refine` is below `resources`. **Resolution (recommend): resolve the target at the caller edge and pass `{ root }` into the metadata-free refine hook** (the boundary already established in `ledger:680-694`). Confirm in Q7.
- `auth.tsx` → `@angee/app` `providers/auth`. `i18n.ts` → collapse into `@angee/app` i18n provider (debt). `errors.ts` → follow its primary consumer.
- Slices C1 (hooks→refine), C2 (auth→app), C3 (i18n→app, errors→owner). C can run after B1, overlapping the back half of Wave A.

### Wave D — Port each addon web + host (mechanical, parallel)

Per addon: change `package.json` deps (drop base/sdk/data, add ui/app/refine), rewrite imports, `pnpm install`, gate. Order (plan `:610`): notes → iam → storage → knowledge → integrate → agents → parties → messaging → operator. Effort by import count: operator 24, integrate 19+8, storage 19+3, iam 16+4, knowledge 11, platform 11, agents 8+4, parties 5, messaging 3, resources 3. Each is an isolated worktree → **parallelizable** once A+B+C are green; serialize the `pnpm-lock.yaml` merge. Then **D-host** (`examples/notes-angee/web`: swap deps, `main.tsx` createApp import base→app — the one flag-day point) and **D-storybook**.

### Wave E — DELETE the old shells (Phase 6), only after zero importers

E1 delete `@angee/sdk` (grep importers empty → remove package + workspace entry + dep lines) → E2 `@angee/data` → E3 old `@angee/base` → E4 verify `urql` only in operator daemon quarantine → **final gate:** repo-wide `pnpm install && typecheck && test && build` + backend `schema --check` + e2e smoke + net-line-drop report.

## Risk handling

1. **Cutover-cache hazard:** `createApp.tsx` (the one `<Refine>`/cache/live owner) moves once, intact, in B2 — never duplicated while the old copy runs. No wave instantiates a second `dataProvider`/`QueryClient`/live engine. Drift-scan gate in B2 & E: `grep -rn 'new QueryClient\|createClient('` stays at its single-owner count.
2. **Green at every step:** build-new-before-flip; old shells keep working untouched until their last importer flips; the only two-path window is a single addon's transient flip, bounded by its gate.
3. **Worktree isolation** for parallel Wave D (`angee ws create … --template dev` or `git worktree`); reconcile the lockfile at merge (serialize that step). A workspace is pinned to its branch.
4. **No flag-day big-bang:** the host createApp-import flip is the only true flag-day risk, isolated to D-host, gated, done after every addon already imports the new packages.
5. **"Cosmetic" framing is itself a risk:** do NOT also rename/fix-local-rows/collapse-i18n mid-move — those are architect-gated follow-ups; mixing them breaks the green-at-every-step guarantee.

## Orchestration shape

```
[STOP] Phase 0 / Q7 doc — architect confirms layering rules + data-hooks dest + auth/i18n placement
   │ (sequential)
   ▼
Wave A (ui: A1→A2→A3→A4) ; Wave B (app: B1 then B2 after A) ; Wave C (C1∥A, C2/C3 after B1)
   │ [STOP] architect confirms refine→resources edge resolution (target-at-edge) before C1
   ▼
Wave D — PARALLEL across 9 addons (one worktree each) after A+B+C green; then D-host, D-storybook (lockfile merge serialized)
   ▼
Wave E — SEQUENTIAL: E1 sdk → E2 data → E3 base → E4 urql verify → final repo gate
   │ [STOP] architect confirms net-deletion report + final gates before merge
   ▼ merge
```
Per cell: implement → `/code-review` scoped to the slice (relocation correctness + no behavior drift) → slice gate (named vitest/typecheck) → commit on green. Commit per slice (revert one move, not a wave).

## Effort / risk per wave

| Wave | Effort | Risk | Why |
|---|---|---|---|
| 0/Q7 | S | Low | Doc only; blocking |
| A (ui) | XL | Med | Largest move; render-DAG order keeps gates green |
| B (app) | M | High | `createApp.tsx` is the single-engine owner |
| C (data) | M | High | Murkiest; DAG-edge + auth/i18n decisions |
| D (addons+host) | L | Med | Mechanical but wide; host flip is the flag-day point |
| E (delete) | S | Low-Med | Pure deletion once importers are zero |

**Branch strategy:** one long-lived integration branch (e.g. `refactor-refine-split`); Q7 + Waves A/B/C/E as ordered per-slice commits; per-addon worktrees for Wave D merged as each gate passes; final merge only after the Wave E repo-wide gate is green + architect confirms the net line drop. Push/PR only when asked.

### Critical files
- `docs/frontend/guidelines.md` (Q7/Phase 0 — the layering contract)
- `packages/base/src/createApp.tsx` (Wave B2 — single `<Refine>`/cache/live owner; cache-hazard epicenter)
- `packages/base/src/index.ts` (Wave A/E — the base barrel = the flip surface)
- `packages/data/src/index.ts` (Wave C — the data surfaces to split)
- `examples/notes-angee/web/package.json` (Wave D-host — the host dep + createApp-import flip)
