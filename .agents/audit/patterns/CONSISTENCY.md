# Pattern & Library Consistency Report

Synthesis of the four inventories in this directory (`be-libs.md`, `be-patterns.md`,
`fe-libs.md`, `fe-patterns.md`), judged against `docs/stack.md` (the declared
canon — one library/shape per concern). Read-only audit; no source changed.

## Headline

- **Library discipline is strong.** 0 unsanctioned frontend imports, no competing
  libraries for any concern (no axios/react-query/Apollo, redux/zustand,
  formik/zod, dayjs/moment, radix), 0 `any`/`as unknown as` in the frontend.
  Backend libraries are equally disciplined (GraphQL=strawberry, REBAC=zed,
  IDs=sqids, encryption=Fernet only, JWT=pyjwt, import/export=import-export).
- **The drift is in PATTERNS, not libraries** — the same concern shaped two ways
  in code — plus **`stack.md` itself being stale** (it claims/locks things the
  code doesn't do). Several items corroborate the earlier decomposition audit
  (marked ✦) — those are highest-confidence.

## A. Standardize on one — pick a canon and migrate (code work, ranked by leverage)

1. **Operator console is a parallel data+view stack.** ✦ `operator/web/src/data/transport.tsx`
   ships its own urql client/provider, hand-written documents, `useOperatorSnapshot`/
   `useOperatorAction`, and raw `Table` — reusing none of the SDK's `useDocumentQuery`,
   authored-mutation runner, stable-deps, or `useDataViewSurface`. `run-action.ts`
   types daemon payloads `Record<string,unknown>` and probes success by a passed-in
   `field` string (inspect-by-key smell). **Canon:** the `@angee/sdk` seams. Migrate the
   console onto them (codegen'd types already come from the daemon SDL — keep that).
2. **GraphQL admin CRUD: `crud()` vs hand-rolled.** ✦ `crud()` is used by *only* the
   notes addon; all IAM admin CRUD (`iam/schema.py`) is hand-rolled with duplicated
   input/assign/delete helpers, because `crud()` can't express elevated
   (`system_context`) writes behind a permission class. **Canon:** add a
   `crud(..., elevated=, permission_classes=)` mode to `base/graphql/crud.py`, migrate
   IAM onto it. (This is the gap the #13 arch-review flagged — now independently confirmed.)
3. **Manager / QuerySet shape.** 5 REBAC managers carry methods inline; only
   `resources` uses `Manager.from_queryset`. **Canon:** decide one (likely
   `from_queryset` for chainable scoped reads) and document it in
   `docs/backend/guidelines.md`; migrate the others.
4. **Enum columns bypass `StateField`.** ✦ `Credential.kind` (`iam/models.py:709`) and
   `Resource.tier` (`resources/models.py:24`) use bare `CharField(choices=Enum.choices)`
   while 4 sibling `status` columns use the `StateField` canon. **Canon:** `StateField`.
   (Note: `AccountStatus`=TextChoices persisted vs `StateFlow`=StrEnum cache value is
   *correct*, not drift — leave it.)
5. **Stable-deps / value-memo idiom forked 4 ways.** ✦ despite `sdk/src/stable-deps.ts`
   declaring itself the one audited home: a byte-identical private copy
   (`authored-hooks.ts:13`), inline `JSON.stringify`/`join` keys (`resource-hooks.ts:101,279`),
   and a sorted-key `stableSerialize` (`grouped-list.tsx:959`). **Canon:** `stable-deps.ts`;
   delete the forks, import the owner.
6. **GraphQL names re-derived from the outside.** ✦ `sdk/src/selection.ts` hand-rolls a
   `pluralize` + first-letter-casing heuristic (its own comment admits irregular plurals
   break) instead of reading names from the generated contract/SDL. **Canon:** the codegen/
   SDL owner.
7. **`ListView` vs `GroupListView` duplicate ~80 lines.** ✦ same toolbar/selection/error/
   dialog shell; GroupListView is a strict superset. **Canon:** one component;
   ListView = GroupListView without grouping.
8. **URL-state boundary dual-write.** `chrome/TopMenu.tsx:94-97` writes the active tab to
   **both** nuqs and Router (one shared flat URL) — `tab` duplicates the filter and can
   desync. **Canon:** one owner (Router search owns route state; nuqs owns the rest — pick
   one for `tab`).
9. **Relative-time hand-rolled.** `NotePage.tsx:354` uses `Intl.RelativeTimeFormat` while
   `list-internals.tsx` correctly uses date-fns `formatDistanceToNow`. **Canon:** date-fns
   (stack.md assigns relative-time to it).
10. **`os.environ` read in code.** `OperatorDaemon._setting` is the only code reading the
    environment directly, vs the host-owns-env canon. **Canon:** route via settings.
11. **File naming + index barrels** inconsistent across `@angee/base` (fe-patterns §4) —
    standardize feature dirs + barrel re-exports.

## B. Reconcile `stack.md` with reality (cheap doc fixes — the canon is itself drifted)

- **`strawberry-django-aggregates`** — stack.md claims framework "wiring to model
  metadata"; only the example addon uses `AggregateBuilder`. Fix the row (it's example-
  level, not framework glue) or add the glue.
- **`django-ninja` + `pydantic` + `python-magic`** — sit in the LOCKED Backend table but
  are neither declared in `pyproject.toml` nor imported. → move to "Proposed, Not Locked".
- **`pyyaml`** — declared + used (resource loading) with no stack.md row → add a row
  (stack.md: "Add a dependency only with an owner row here").
- **Branded boundary types** — stack.md promises them ("Branded boundary types"); every id
  (`useResourceRecord`, mutation `id`, `objectId`, operator `token`) crosses as bare
  `string`. → adopt branded id types, or drop the claim. (Decision.)
- **5 declared-but-unused `@angee/base` deps** (`@dnd-kit/core`, `@dnd-kit/sortable`,
  `@floating-ui/react-dom`, `valibot`, `use-debounce`) and **6 aspirational stack.md rows**
  with no dep + no code (`@xyflow/react`, `react-dropzone`, `react-json-view-lite`,
  `ansi-to-react`, `simple-icons`, `@lobehub/icons`) → prune or move to "Proposed."

## C. Confirmed CONSISTENT — do not touch

GraphQL=strawberry-only · REBAC=zed (const-admin `admin->member` 8/8 identical) ·
IDs=sqids everywhere · encryption=Fernet only inside `EncryptedField` ·
import/export=django-import-export · JWT=pyjwt · channels/daphne · history(simple-history,
whole-row) vs revisions(reversion, per-field) split is *principled* · styling=`cn()`+`tv`
sharing one merge config · icons=lucide via central registry · data-fetching=urql
everywhere · GraphQL types codegen'd from the daemon SDL · validation tiering
(ValidationError/ValueError/ImproperlyConfigured by layer) · `compose_defaults` purity ·
`from_*` factories · `AccountStatus`/`StateFlow` (persisted-vs-cache, correct).

## How this feeds the fix loop

Bucket A items overlap the decomposition audit (✦) — fold them into the same per-unit
fix queue (`crud()` elevated → iam; stable-deps/GraphQL-names → sdk; ListView dup →
packages/base; operator console → operator). Bucket B is a single cheap `stack.md`
reconciliation commit. Standardizing a pattern is one decision + one migration each —
do them owner-first like the decomposition fixes.
