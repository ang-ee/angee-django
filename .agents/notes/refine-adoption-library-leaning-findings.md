# Refine Adoption — Library-Leaning & Decomposition Findings

Date: 2026-06-24
Source: three independent reviews of the in-flight `refactor-refine` branch
(architecture-reviewer over the frontend, django-reviewer over the backend,
plan-reviewer over the remaining plan lanes), commissioned to answer one
question — *are we leaning hard enough on the libraries, and what other
decomposition patterns apply?*

Governing plans this amends:
[`refine-adoption-hasura-todo.md`](../plans/refine-adoption-hasura-todo.md) and
[`refine-adoption-refactor-plan.md`](../plans/refine-adoption-refactor-plan.md).

## Follow-up

These findings led to a **greenfield frontend rebuild** decision (2026-06-24): a
clean four-package stack on Refine rather than continued incremental refactor. See
[`refine-greenfield-rebuild-plan.md`](../plans/refine-greenfield-rebuild-plan.md),
which supersedes the incremental Lane H–L framing in
[`refine-adoption-hasura-todo.md`](../plans/refine-adoption-hasura-todo.md) and
folds in the backend owner-corrections below.

## Verdict

The **dialect** leaning is genuinely strong: `hasura_resource(...)` composes
`strawberry-django-hasura` + `strawberry-django-aggregates` + `django-zed-rebac`
+ `django-sqids`, aggregate math is wired free, and authorization stays
rebac-owned. The gap is consistent across backend and frontend and has one
shape: **the refactor has been adding the new owner without deleting the old
one**, so several library owners are currently *shadowed* by Angee code rather
than *delegated* to. The plan's own greenfield principle #1 ("old and new never
run together; no coexistence layer") is violated in the seam meant to enforce
it. Net: not yet leaning hard enough; the work is real but the competing owners
have not been removed.

The correction is a single principle applied everywhere: **re-gate every
remaining rebind on net deletion, not relocation** — each rebind must delete its
old owner in the same change.

## Theme 1 — Two of everything (the dominant library-leaning gap)

Verified in the current tree. Critical.

- **Two full data/cache/invalidation stacks run concurrently** — refine +
  react-query (`@angee/data`) *and* urql + graphcache + a hand-rolled
  relay-registry (`@angee/sdk`), both mounted in
  `packages/base/src/createApp.tsx` (refine `liveProvider` ~264 *and*
  `RelayInvalidationProvider` ~559). Two GraphQL versions (15 vs 16), two
  clients, two live-subscription paths.
- **The thin owner depends on the package it replaces.**
  `packages/data/package.json` declares `@angee/sdk` (`workspace:*`), and
  refine-backed hooks reach back into the SDK's urql invalidation registry —
  `packages/data/src/authored-hooks.tsx` imports `useInvalidateModels` /
  `useRegisterModelsRefetch` from `@angee/sdk` instead of refine's
  `useInvalidate` (which the same package already uses correctly in
  `hooks.tsx`). Dependency points outward toward the detail it is meant to
  delete (AGENTS.md: "dependencies point toward stable owners… never outward").
- **`@angee/data` has itself become a second GraphQL document engine** —
  `packages/data/src/operations.ts` (~884 LOC) string-builds list / group /
  facet / aggregate documents and `parse()`s them, backed by a freshly
  *duplicated* `selection.ts` + `stable-deps.ts`. The codegen
  `TypedDocumentNode` / `meta.gqlQuery` owner that `docs/stack.md` names is
  bypassed. Much of the "done" deletion work was a **sideways lift**
  (SDK → data/base), not a deletion — net code did not drop. `selection.ts`,
  `stable-deps.ts`, and `i18n.ts` now exist in *both* packages.

Fix: collapse onto refine + react-query as the sole live/data/invalidation
owner, route authored ops through the stock provider transport + `meta.gqlQuery`
codegen documents, then delete `relay-invalidation.tsx`, `relay-registry.ts`,
`graphql-provider.tsx`, `graphql-client.ts`, `cache-config.ts`,
`schema-object-types.ts`, `document-subscription.ts`, and the duplicated
`selection`/`stable-deps`. Invert the dependency so `@angee/data` no longer
imports `@angee/sdk`.

## Theme 2 — Re-deriving facts the owner already holds (incl. the one live correctness risk)

Find-the-owner / one-source-of-truth.

- **Backend, drift risk (High).** `angee/graphql/data/hasura.py` and
  `metadata.py` re-derive group-key aliases (`f"{path}_id"`), bucket-range keys
  (`f"{key}_{granularity}_range"`), the node/aggregate type prefix (by
  `endswith("Aggregate")` string-scan), and scalar families by parallel
  hand-rules — when `strawberry-django-aggregates` (`group_by_alias`, the typed
  `<Model>GroupKey`, `BucketRange`, `default_operators_for`) and the built
  `HasuraResource.types` already hold the canonical names. The frontend trusts
  these key strings to build queries; if the aggregates lib changes an alias the
  SDL key changes but Angee's metadata key does not, and the frontend silently
  selects a non-existent field. **Read the names off the built types; do not
  recompute them.** (Verified against installed library source.)
- **Frontend, duplication (High).** `packages/sdk/src/model-metadata.tsx`
  (~952 LOC) still parses the whole SDL at boot (`fieldMetadataFromSchema`,
  wired in `graphql-provider.tsx`) to rebuild a field kind/scalar/relation
  inventory that the backend `angee.resources` artifact already emits
  (`DataResourceFieldMetadata`). Two parallel shapes for one fact; this is what
  keeps `graphql@16` + `schema-object-types.ts` alive. Project `ModelMetadata`
  from `resource.fields` and delete the SDL walk.

## Theme 3 — One fact written N times → make the owner derive it

DRY / behavior-on-owning-object.

- **Backend.** Across the ~16 migrated addon resources: an 8×-copied
  `_aggregate_queryset` helper (it `getattr`-decodes `scoped_for_aggregate`,
  which already lives on `AngeeQuerySet`), ~24 near-identical
  `get_queryset=lambda info: Model.objects.all()` lambdas, a per-resource
  `write_backend` / `id_decode`, and the FK→model relation map declared 2–3×
  per resource (`field_id_decode` *and* `public_id_fields` — the same fact the
  model's `_meta` FK already holds). Default all of these inside Angee's
  `hasura_resource(...)` wrapper from `(node, model)`, so each addon call reads
  as what it actually is: an **allowlist declaration** (filter / sort /
  aggregate / group / insert / update), with the genuine exceptions (storage
  `FolderWriteBackend`, IAM password hashing, messaging create-disabled)
  standing out instead of buried.
- **Frontend.** An Angee-private filter dialect
  (`RESOURCE_VIEW_LOOKUP_OPERATORS` in `resource-view-model.ts`) sits between
  refine `CrudFilters` and Hasura `_bool_exp` and is hand-mapped to *both*
  (`refineFiltersFromAngeeFilter` and `hasuraWhereFromAngeeFilter` in
  `filter-codec.ts` are the same switch twice). Two `i18n` interpolation/fallback
  modules (`packages/data/src/i18n.ts` + `packages/sdk/src/i18n.ts`). Collapse to
  one canonical map each.

## Theme 4 — Polymorphism over type-switch

- **Backend.** Four parallel `isinstance(field, models.XField)` ladders decide
  scalar / measure-ops / widget / kind (`hasura._scalar_for_field`,
  `hasura._measure_ops_for_field`, `metadata._surface_field_scalar_or_none`,
  `metadata._field_widget`), plus `_is_to_one_relation` defined in *both*
  `hasura.py` and `metadata.py`. Collapse to one field classifier reading the
  post-composition Strawberry surface (the authoritative owner); push "valid ops
  for this scalar" to the aggregates owner (`default_operators_for` is already
  exported). *(Confidence: the duplicate ladders are verified; a column where the
  two scalar paths actually diverge is latent, not demonstrated.)*
- **Frontend.** `packages/base/src/views/ListInternals.tsx`
  (`bucketFilterForGroup` / `bucketDateRange` / `addGranularity` /
  `jsonBucketValue`) reconstructs a child `where` from a bucket key by switching
  on `dimension.scalar` / `field.kind`. The `docs/stack.md` "Hasura Dialect
  Rule" explicitly forbids keeping grouped date/JSON drill-down semantics
  frontend-only — the backend `_groups` group should emit its drill-down
  predicate (it already returns a typed `rangeKey`); the frontend forwards a
  `where`.

## Theme 5 — Deferred owner-fixes that have already become permanent debt

"Investment not debt / never quick-fix to make it run."

- **Per-bucket name-converter seam (parked → now overdue).** Clarification #2 in
  the narrative plan names the clean enabler — one `SnakeNameConverter` /
  `hasura_config()` per Hasura schema bucket on `AngeeSchema` — and says the
  per-field `pin_snake_wire_names` pins "drop once a schema is fully Hasura."
  Lane E reports **every schema is now Hasura**, so the trigger has fired but the
  seam was never built. Worse, `hasura.py:pin_snake_wire_names` is a *weaker*
  duplicate of the adapter's own `_pin_snake_wire_names` (no cyclic-graph `seen`
  guard, no nested recursion) and has **leaked out of the adapter into addon
  `schema.py` files** (`iam`, `agents`, `iam_integrate_oidc`). Promote the seam
  into an active lane and delete the pins + addon call sites.
- **Open decisions left ajar are competing owners, not neutral deferrals.**
  `urql` is load-bearing (auth-cache reset via `useResetClient`, the SDK live
  engine) — leaving it "open" keeps a second client/cache alive; close it as a
  scheduled deletion matching the already-locked `docs/stack.md` row. `zod` is in
  `docs/stack.md` + the manifest; a `zodResolver` is now wired in `FormView`
  (Lane H), so confirm it has a consumer or drop the row. `valibot` is correctly
  confined to JSON-scalar narrowing per `docs/frontend/guidelines.md` — keep it
  scoped or delete; do not ship two validation libraries.

## Theme 6 — Plan/code drift

The plan can no longer be trusted as status of record: it over-claims (lanes
marked "done" whose old owner is still mounted) and under-claims (FormView was
already on `@refinedev/react-hook-form` while Lane H listed it not-started). A
new SDK file `authored-subscription.ts` exists and is exported but appears in no
deletion ledger. Reconcile the Lane K ledger with the real `@angee/sdk/src`
inventory, and rewrite each Lane K line to state the **caller fan-in it clears**,
so a checked box equals a real removal, not a relocation.

## Recommended deletion-driven sequence (the keystone first)

1. **Collapse the dual live/data engine onto refine + react-query** and remove
   urql: one invalidation owner (`liveProvider` + `useInvalidate`), delete
   `relay-invalidation`/`relay-registry`/`graphql-provider`/`graphql-client`/
   `cache-config`/`schema-object-types`/`document-subscription`. This is the
   keystone — it unblocks honest deletion and stops `@angee/data` importing
   `@angee/sdk`. Do it **before** any further Lane J addon migrates against the
   dual engine.
2. **Project `ModelMetadata` from the backend artifact**; delete the
   SDL-introspection engine (drops `graphql@16`).
3. **Land the per-bucket name-converter seam**; delete `pin_snake_wire_names`
   + addon call sites.
4. **Read group-key/scalar/range/node-prefix names off the built types**
   (kills the drift risk); collapse the four field ladders to one classifier.
5. **Default the standard glue inside `hasura_resource(...)`**; delete the
   N-copy boilerplate so addon calls become allowlist declarations.
6. **Close the urql / zod / valibot / FormView decisions** to match the code and
   reconcile the Lane K ledger.

## Confidence

- Themes 1, 2 (backend), 3, 5 — verified directly against the repo and installed
  library source. High.
- Theme 2's frontend duplication and Theme 4's backend scalar *divergence* — the
  duplicate code paths are verified; an actually-diverging column is latent, not
  demonstrated. Medium.
- Theme 6 (`zod` consumer) — `zodResolver` is now wired in Lane H; confirm a live
  consumer before dropping the dependency.
