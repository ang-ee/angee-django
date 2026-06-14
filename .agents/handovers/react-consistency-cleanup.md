# Handover: React consistency & DRY cleanup (continuation)

## Your task

Continue the frontend consistency/DRY cleanup of the Angee React layer
(`packages/sdk`, `packages/base`, `packages/storybook`, and each addon's `web/`).
The goal: make the frontend **fully consistent and DRY — code-wise and UX-wise**,
by finding the owner of each repeated fact and removing the copies. Work through
the remaining themes in the plan, one well-scoped slice at a time, verifying and
committing between each.

The exhaustive, line-level task list is the source of truth — **read it first**:

- **`.agents/plans/react-consistency-todo.md`** — the phased TODO. `[x]` done,
  `[~]` in progress, `[ ]` open. Each item names the owner that absorbs the fix.
- **`.agents/plans/react-consistency-cleanup.md`** — the original cross-engine
  audit (themes T1–T19) with rationale + evidence behind each item.

This handover is the orientation; the todo is the work.

## Where things stand (as of this handover)

- **Workspace:** `/Users/alexis/Work/angee/angee-django/.angee/workspaces/react-consistency`,
  branch `workspace/react-consistency` (parent `main`). Working tree clean; all
  commits are **local to the workspace branch — run `/push` to publish** (commits
  are not pushed automatically).
- **Gate is green** repo-wide: `pnpm run typecheck && pnpm run test && pnpm run build`.
- **Done** (each landed as: delegate heavy edits → full gate → fresh sub-agent
  review → fix findings → commit):
  - **T1** two-axis color (`tone` × `variant`/fill via `lib/tones.ts`) — landed earlier.
  - **T5** icon-registry-only — every icon routes through `<Glyph name>`; **no raw
    `lucide-react` imports remain outside `chrome/icon-registry.ts`**. `Glyph` has a
    `strokeWidth` passthrough (one owner); `useIcon` falls back to static `baseIcons`.
  - **T6** i18n-commit — all base copy + all six addon consoles route through
    `useBaseT()` / `use<Addon>T()`, both built on the shared SDK
    `useNamespaceT(ns, fallback)`. **Complete.**
  - **Command palette** — `Spotlight` is live as ⌘K via a new `chrome/CommandPalette`
    (nav commands from `MenuTree.navigableItems()` + `useNavigate`); dead
    `GlobalSearch` deleted; e2e repointed to the palette button.
  - **T16** TopMenu de-leaked to a generic presentational tablist (no `DEFAULT_TABS`,
    no `DataViewFilter`).
  - **T15** `defineBaseAddon()` seam exported from `@angee/base`; all 8 manifests migrated.
  - **T13** previews via build-time composition — module-global registry +
    `registerPreviewProvider` + import side-effect **deleted**; addons contribute via
    a `previews` manifest field; built-ins are a static const.
  - **T2b** `UserMenu` rebuilt on the real `DropdownMenu` primitive (native menu a11y).
  - **T8 (partial)** one `optionLabel(options, value)` owner in `widgets/types.ts`
    (routed select/combobox/many2one/statusBadge/many2many).
  - **T10 (partial)** one generic `useQueuedDialog<TOptions,TResult>` in `ModalsHost`
    (confirm + prompt).
  - **Docs** — `docs/frontend/guidelines.md` now encodes the landed rules.
  - Phase 6 (locked architecture decisions) is done **except T17**.

## What remains (recommended order)

**✅ COMPLETE (2026-06-15).** Every theme below LANDED — T17, the Phase 5 de-forks,
the remaining T8, T2, T4, T10, Phase 7, and a final storybook/i18n/drift/docs pass —
each gate-green + sub-agent-reviewed + committed (gate: typecheck + 399 tests + build).
`main` was merged in (`0cf22ca3`, 11 conflicts resolved keep-both-intents; login
redesign + OAuth + agents provisioning + VCS + operator changes integrated with the
cleanup intact). The only non-done items are **by decision**: **T19** storybook
fragments (KEEP), the leave-separate scopings (recorded per-line in the todo), and the
**backend** dev-template operator-port note (out of scope for this React effort).
Branch is still local — `/push` to publish. The list below is the historical plan;
see `react-consistency-todo.md` for authoritative per-line status.

(historical plan — all items now done or decided-leave-separate:)

1. **T17 — forms onto the DSL** (largest): replace inline `<form><label>` in
   iam/operator with `Field`/`FormView`/a shared `LabeledInput`; register
   drive/vault relation-create via `defineAddon` `forms:`; `Notebook.Tab` onto
   `PAGE_ELEMENT_SLOT`; make `FormView` Star/Share **host-provided** (slot/action —
   the chosen default) and replace `text-amber-500` with a token.
2. **Phase 5 de-forks** (large, several items): data-view shell (collapse
   `ListView`/`RowsListView`, fold `GroupedList`), one field stack
   (`field`/`form`/`form-layout`), header collapse
   (`CollectionHeader`/`SurfaceHeader`/`RecordHeader` → `PageHeader`),
   `ScopedTreeExplorer` (storage ≈ knowledge), `PageChrome`/`TwoPaneFrame`,
   a shared date popover (date/datetime) + a `ChipList`/`TokenList` primitive
   (tagInput/many2many), `FormSectionKicker`/`ControlRow`,
   `InlineNameField`/`RefreshingBadge`/`SectionNav`/`MetricTile`. The
   dropdown/context-menu factory is **deliberately deferred** (real API divergence
   — see the todo note); their shared surface is already deduped via `POPUP_BASE`.
3. **T8 remainder** (find-the-owner): `recordSubtitleParts`/aggregate-key/changed-field
   sniffing → SDK/model metadata; `FormView` field-behavior decoders → widget/field
   resolver; lookup-operator guards → `data-view-model`; **AppChooser/TopMenu active
   logic → `isActive(pathname)`/`hasActiveDescendant(pathname)` on
   `ChromeMenuNode`/`MenuTree`** (clean, and `menu-tree.ts` is fresh from the
   palette work — good next pick); model-metadata humanization → rendered binding.
4. **T2** move `DEFAULT_STATE_TONE_VALUES`/`stateToneFromValue` business semantics
   out of base into model/field metadata or explicit caller mapping.
   **T10 remainder**: one `useDocumentMutation` SDK hook; collapse
   `useStableMeasures` into a generic `useStableValue`.
5. **T4** state-surface API: align `EmptyState`/`InlineEmpty`/`LoadingPanel`/
   `ErrorBanner` prop vocab; the `CollectionStatus`/`DataViewFrame` frame; delete
   `Chatter`'s local `EmptyState`; preview renderers own their own loading/error.
6. **Phase 7 polish**: `pagerVariants`/`treeVariants`; `Tree.iconColor:string` →
   token-backed `iconTone`; default `size` standardization; one namespace-export
   convention; storybook cleanups; `useOperatorSnapshot` `want*` → `SECTION_KEYS`.
7. **Docs** — extend `docs/frontend/guidelines.md` for T4 (state-surface) and T17
   (forms-DSL) once those owners land. `docs/stack.md` needs no change (no new libs).
8. **Final drift sweep** — after the above land, grep for remaining `from
   "lucide-react"` outside the registry, hardcoded copy, `.join(" ")` class merges,
   and the retired `error` tone; close any stragglers.
9. **T19 fragments** — **decision: keep** (deferred); revisit per fragment on first
   real consumer. Do not delete.
10. **Infra (not React, tracked)** — the dev workspace template hardcodes the
    operator port `9000` instead of allocating it per workspace; fix
    `templates/workspaces/dev` (and/or `stacks/dev`) process-compose to use
    `${ports.operator}`.

## Locked decisions (do not relitigate)

Set by the user on 2026-06-14:

1. **i18n = commit** (route copy through the i18n layer; per-addon namespaces).
2. **Color = two orthogonal axes** (Radix model): `tone`/palette × `variant`/fill
   (solid/soft/surface/outline/ghost). `default`→`neutral`, `error`→`danger`.
3. **`defineBaseAddon` = yes** (done).
4. **Speculative storybook fragments (T19) = keep** for now (tracked, not deleted).
5. T5 primitives = "Glyph fallback, then route" (done). Command palette = "wire
   Spotlight live, delete GlobalSearch" (done). T16 default = de-leak to notes
   route owner — landed as a generic tablist (no live consumer wires it yet).

## How to work (the loop that held quality)

1. **Read the owner before changing it** — the todo names the absorbing owner; read
   that file + its callers (re-read after editing, per the constitution).
2. **Delegate heavy/mechanical edits** to a sub-agent (file-isolated, precise
   recipe, self-verify with `pnpm --filter @angee/<pkg> typecheck`). Good for bulk
   i18n routing or N-file migrations. Keep design/judgment calls yourself.
3. **Gate**: `pnpm run typecheck && pnpm run test && pnpm run build` from the
   workspace root (per-package: `pnpm --filter @angee/<pkg> typecheck|test`).
4. **Review between phases** (the user requires this): spawn a **fresh**
   `react-reviewer` and/or `architecture-reviewer` sub-agent on the staged diff.
   Fix the actionable findings (they have repeatedly caught real issues: a lost
   `strokeWidth`, an orphaned e2e assertion, doc/code drift). Then commit.
5. **Commit between phases** (the user said so — commit, do not push). Update the
   todo (`[x]` + a one-line what/why) in the same commit.
6. Continue autonomously; the user does not want to be stopped for check-ins.

## Key facts / gotchas (this repo + this session)

- **Verify gate** = `pnpm run typecheck && pnpm run test && pnpm run build` from the
  workspace root. `agents/web` test script uses `--passWithNoTests`. The example
  host build needs `runtime/schemas/*.graphql` (generated, gitignored) — emit with
  `uv run examples/notes-angee/manage.py angee build` + `… schema` if a clean build
  complains they're missing.
- **Bash cwd persists between calls** in this harness — prefer absolute paths; a
  repeated `cd packages/base/src` will fail after the first.
- **`useNamespaceT(ns, fallback)`** (SDK `runtime.ts`) is the translator owner;
  `useBaseT` and every addon `use<Addon>T` build on it (so a component renders
  English provider-less — this fixed iam's failing tests). Keep that pattern.
- **i18n prop defaults**: never call `t()` in a default parameter. Default the prop
  to `undefined` and coalesce `?? t("key")` in the body (preserves the public API).
- **`useMenus()`/`usePreviews()` are cast** to the rendered type in base
  (`as readonly ChromeMenuItem[]` / `as readonly PreviewProvider[]`) — the SDK keeps
  only the structural minimum (id) for collision detection; base owns the rich shape.
  This is the established headless→rendered seam; mirror it.
- **Build-time composition only** — never reintroduce a runtime registry/side-effect
  (T13 removed the last one). Contribute via the manifest + `composeAddons`.
- **`composeAddons` is fail-fast on id (icon/route/menu/i18n/widget/form/preview)
  and only runs at app boot — `typecheck`/`build` miss collisions.** Adding a name
  to base `baseIcons` collides with any addon already contributing it (this bit the
  T5 markdown work: base `link` vs knowledge's `link`). After touching `baseIcons`
  or an addon's `icons`, run `pnpm run test` — `examples/notes-angee/web/src/
  addon-composition.test.tsx` composes the full host addon set as the guard.
- **Workspace rules**: never `git checkout`/`switch` inside the workspace (make a new
  workspace for a different branch); don't edit generated `runtime/`; scratch only in
  gitignored locations; no secrets in `.agents/`.
- **`angee dev`** is the only supported way to bring the stack up; this workspace has
  its own allocated ports. (There's a tracked infra note in the todo about the dev
  template hardcoding the operator port 9000 — backend/template, not React.)
- Knowledge goes in the repo: durable rules → `docs/frontend/guidelines.md`
  (Pitfalls section); work-state → `.agents/`. Do not use private agent memory.

## Commit range

This session: `572cb641` (T5 primitives) .. `HEAD`. Includes the 13 theme commits,
this handover, and a follow-up fix `6d535d65` (base/knowledge `link` icon
collision found by `angee dev` + a full-addon-composition test guard).
`git log --oneline main..HEAD` shows the full branch — it also includes the
earlier work (T1 color, T4 empties, all-addon i18n, etc.), all already reflected
as `[x]` in the todo.
