# Plan: Finish computed-source resources (frontend migration + remaining stages)

Self-contained handoff plan. A fresh agent can execute this end-to-end. It
finishes the work begun in
[`hasura-row-resource-non-model.md`](./hasura-row-resource-non-model.md): make
every non-model console source a Hasura resource and **delete the hand-rolled
client engine `packages/base/src/views/local-rows.ts`** by routing those pages
through the one shared `ListView`/`useList` path.

## What is already done

- **Stages 0–2 (committed)** — the framework foundation:
  - Sibling lib `ang-ee/strawberry-django-hasura` **0.3.0** (branch
    `feat/run-query-resource`, commit `826e1f5`): `hasura_run_query_resource` +
    `RowSource`/`InMemoryRowSource` + the in-memory `_bool_exp` evaluator
    (`where_matches`/`apply_in_memory`). Angee pins it via a `[tool.uv.sources]`
    path source.
  - Angee (branch `refactor-claude`, commit `af489c57`): `hasura_model_resource`
    (renamed from `hasura_resource`), **`hasura_pydantic_resource`**
    (`angee/graphql/data/pydantic_resource.py`), model-optional
    `make_data_resource_metadata` (`metadata.py`), pydantic locked in
    `docs/stack.md`.
- **Stage 3 backend (done, UNCOMMITTED)** — `addons/angee/platform/schema.py`
  adds `PlatformAddonRow` (pydantic) + `_addon_rows_for` provider +
  `_ADDON_RESOURCE = hasura_pydantic_resource(... name="platform_addons",
  model_label="platform.Addon" ...)`, registered in the `console` bucket.
  Verified: `angee build` + `schema` green; the console SDL emits
  `platform_addons(where/order_by/limit/offset)`, `_aggregate { aggregate {
  count } }`, `_by_pk`, `platform_addons_bool_exp`, `platform_addons_order_by`.
- **Architecture decision (done, UNCOMMITTED)** — recorded in
  `docs/frontend/guidelines.md` ("The data view's client/server boundary is a
  row-model choice…"). Grounded in prior art: AG Grid row models, TanStack's
  built-in client row models (Angee's grid IS TanStack), MUI `*Mode`; admin
  data-providers delegate to the server by default and reserve client-side for
  small/local data; computed data is server-queryable only as a view/function,
  else display-only.

**First action for the executor:** commit the Stage-3 backend + decision-doc as
a clean base (`git add -A && git commit` on `refactor-claude`).

## The architecture to build (the core decision)

The console computed sources are **small (≤500 rows), admin-only**. Per the
prior art, small/computed collections use a **client-side row model**: one fetch,
then filter/sort/paginate/**group** in the browser. Large model-backed resources
stay **server-side** (Hasura `where`/`order_by`/`limit` + the `_groups`
aggregate). `local-rows.ts` is the legacy hand-rolled client engine; it is
replaced by the grid's owned pipeline, unified into the one `ListView`.

Two layers, two consumers:
- **Backend `hasura_pydantic_resource`** stays — it is the uniform **fetch +
  metadata + MCP** surface (agents query computed data with the same `_bool_exp`).
- **Frontend `ListView`** processes a **client** resource in the browser
  (fetch-all + client row models + the existing `groupRows()`), and a **server**
  resource as today.

### The "client-side" resource signal (load-bearing)

Add an explicit **`rowModel: "client" | "server"`** to resource metadata
(prior-art name; default `"server"`):
- Backend: `make_data_resource_metadata(..., row_model="server")` gains the kwarg;
  `DataResourceMetadata` gains the field (wire name `rowModel`);
  `hasura_pydantic_resource` passes `row_model="client"`; `hasura_model_resource`
  leaves the default `"server"`. Wire it through `serialize_data_resources`.
- Frontend: the emitted `angee.resources` metadata type
  (`packages/resources/src/metadata.tsx` `DataResourceMetadata`) gains
  `rowModel`. `ListView` reads `modelMetadata.resource.rowModel === "client"`.

Do NOT derive the signal from "no groups capability" alone — a *large* model
resource can also lack groups, and must not fetch-all. The flag is explicit.

## Stage F1 — the enabler: client-side row model in the data view

**Highest risk; do first and verify before any fan-out.** Lives in a delicate
shared primitive (see the freeze-guard at `resource-view-surface.ts:561` — render
storms with grouping + virtualization).

Files:
- `angee/graphql/data/metadata.py` — `row_model` field + wire (Stage-signal
  above) + a test.
- `angee/graphql/data/pydantic_resource.py` — pass `row_model="client"`.
- `packages/resources/src/metadata.tsx` — `rowModel` on the metadata type.
- `packages/base/src/views/resource-view-surface.ts` — in
  `useResourceViewSurface`, add a **client branch**: when
  `modelMetadata.resource.rowModel === "client"`, fetch the full set (useList with
  no server pagination — a high `pageSize` cap, e.g. 1000; **`log`/warn if the
  count hits the cap** — no silent truncation), and apply
  filter/sort/paginate **client-side**. Reuse the Angee-dialect matcher from
  `local-rows.ts` (`localRowsFilter`/`localRowsSort`) as the client engine (or
  wire TanStack `getFilteredRowModel`/`getSortedRowModel`/`getPaginationRowModel`
  with the matcher as the global `filterFn`). This **absorbs**
  `useRowsResourceViewSurface`; that hook + `createLocalRowsDataSource` go away in
  Stage D.
- `packages/base/src/views/ListView.tsx` — for a client resource, keep
  `groupedListMode = false` (do NOT take the server `GroupedListBody`/`_groups`
  path), so the **flat list groups via the existing `groupRows()`/`listItems`**
  (already rendered by `FlatListBody`). Gate: `groupedListMode = view==="list" &&
  groupDimensions.length>0 && rowModel!=="client"`.
- `packages/base/src/views/list-view-utils.ts` — `validResourceViewGroupStack` /
  `groupSupportedByResource` must keep a **plain-field** group (validate against
  resource *fields*) when `rowModel==="client"`, instead of requiring a server
  group dimension.

Acceptance (verify with the existing `platform_addons` resource):
- `ListView resource="platform.Addon"` renders the rows via `useList`.
- Filter / sort / paginate work client-side; **group-by-namespace works in list
  view**; no `platform_addons_groups` query is issued (it doesn't exist).
- Build + `angee dev` + browser: the Addons list groups by namespace with no
  console errors and no render-storm freeze.

## Stage F2 — prove the pattern: migrate the Addons page

`addons/angee/platform/web/src/`:
- `index.ts` — tag the route: `{ name:"platform.addons", …, resource:"platform.Addon" }`.
- `views/AddonsPage.tsx` — replace `usePlatformAddonRows()` + `<RowsListView>`
  with `<ListView resource="platform.Addon" columns=… groupOptions=…
  defaultGroup={{field:"namespace"}} pageSize={…}/>`. Adapt columns to the **raw
  resource fields** (snake: `label`, `namespace`, `kind`, `model_count`,
  `field_count`, `resource_count`, `depends_on`, `depended_by`) — keep the custom
  `render` (`TextRouteLink`/`Badge`/`Code`/`LinkedChips`); `Column.render` is the
  same `ColumnDescriptor.render` API. Drop `selectPlatformAddonRows` /
  `usePlatformAddonRows` (the addon detail/graph keep `useAuthoredQuery` +
  `PlatformExplorer`).
- Keep `AddonDetail` and the model graph on the authored `PlatformExplorer`
  document (they are NOT local-rows consumers).

Acceptance: the Addons page is visually + functionally equivalent (columns,
links, grouping, sort, filter) over `useList`. This is the de-risking gate —
**do not fan out until F1+F2 are browser-verified.**

## Stage B — backend: the remaining resources

Each: add the resource, register in the right bucket, verify SDL + metadata
(`angee build` → `schema` → grep the console SDL). Providers enforce the same
REBAC gate the authored query did (return `[]` / `.none()` for a non-admin
actor).

- **B1 platform models + fields** (`addons/angee/platform/schema.py`): decompose
  `_build_explorer` into row providers.
  - `PlatformModelRow` (pydantic) — **add an explicit `id` = `label`** (the
    strawberry type keys by `label`, has no `id`); fields per the existing
    `PlatformModel` minus the nested `fields` list (the nested `fields` stay a
    detail concern). `name="platform_models"`, `model_label="platform.Model"`.
  - `PlatformFieldRow` (pydantic) — **flattened across all models**, each row
    carrying `model` + `addon` context + a synthetic `id`
    (`f"{model}.{name}"`). `name="platform_fields"`, `model_label="platform.Field"`.
  - Providers gated by `platform_can_read()`.
- **B2 IAM roles + grants** (`addons/angee/iam/schema.py`): pydantic (computed —
  tuples joined with REBAC schema-AST labels, no clean queryset). `roles` →
  `iam.Role`; `grants` → `iam.Grant`. **Keep the grants revoke mutation** as-is
  (an authored single-id action — NOT a local-rows concern; the LIST migrates,
  the action stays). Providers gated by `_ADMIN_PERMISSION_CLASSES`-equivalent
  (admin actor check; non-admin → `[]`).
- **B3 native read-only resources** (the queryset-backed sources — use
  `hasura_model_resource`, not pydantic):
  - IAM **relationships** — `hasura_model_resource(RelationshipType,
    model=active_relationship_model(), get_queryset=<admin-scoped, .none() for
    non-admin>, insert=False, update=False, delete=False)`. Drop the authored
    `relationships` query.
  - Resources **ledger** (`addons/angee/resources/schema.py`) —
    `hasura_model_resource(ResourceLedgerType, model=Resource,
    get_queryset=<admin-scoped>, insert/update/delete=False)`. Drop the authored
    `resourceLedger` query.

Note: model resources are `rowModel="server"`; but these console lists are also
small — decide per resource whether to mark them `rowModel="client"` (admin
grid, client grouping) by passing a client get-all, or keep server. Default:
**relationships/ledger stay server** (real querysets, real `_groups` available);
**platform/iam computed stay client**.

## Stage P — migrate the remaining pages (parallel after F1+F2)

Each page: tag its route `resource:`, replace the `AuthoredRowsList`/`RowsListView`
with `ListView resource="…"`, adapt columns to raw fields, preserve
renders/grouping. Pages (from the consumer inventory):
- Platform **Models** (`platform/web/src/views/ModelsPage.tsx`), **Fields**
  (`FieldsPage.tsx`) — `platform.Model` / `platform.Field`; keep the addon/model
  scope filters (now `filter=` on ListView).
- IAM **Roles** (`iam/web/src/views/RolesPage.tsx`), **Grants** (`GrantsPage.tsx`
  — keep the revoke `<Action>`), **Relationships** (`RelationshipsPage.tsx`) —
  `iam.Role` / `iam.Grant` / the relationships resource.
- Resources **Ledger** (`resources/web/src/views/ResourcesPage.tsx`) — the ledger
  resource.

## Stage D — delete the in-memory path

Once no page imports them:
- Delete `packages/base/src/views/local-rows.ts` (keep only the Angee-dialect
  matcher if Stage F1 reused it — move it beside the surface, or inline) +
  `local-rows.test.ts`.
- Delete `AuthoredRowsList`, `useRowsResourceViewSurface`, `RowsListView`, and
  `useAuthoredRows` if now unused (`useAuthoredQuery` stays — detail/graph use it).
- Update `addons/angee/platform/web/src/lib/explorer.ts` (remove the
  `usePlatform*Rows` list hooks; keep `usePlatform*` detail + graph).
- Run the frontend primitive-drift scan (`docs/frontend/guidelines.md` → Checks)
  and explain/clear every hit.

## Stage V — verify end-to-end

Backend (from repo root): `uv run examples/notes-angee/manage.py angee build` →
`makemigrations base notes` (expect **no new migrations** — no new models) →
`migrate` → `rebac sync` → `resources load` → `schema` → `schema --check`.
`uv run pytest` (Angee) green; sibling `uv run pytest` green.

Frontend: `pnpm run typecheck && pnpm run test && pnpm run build`; the
`addon-composition.test.tsx` passes.

App: `angee dev` from the repo root; browser-verify each console page — Addons,
Models, Fields, Roles, Grants, Relationships, Ledger — for
filter/sort/paginate/group/custom-render and the grants revoke action.

## Open decisions & risks

- **`rowModel` flag** is the recommended signal (explicit, prior-art-named). The
  alternative (derive from capabilities) is rejected — it can't tell a small
  computed resource from a large model one.
- **Fetch-all cap:** client resources fetch up to a cap (e.g. 1000); `log` a
  warning if a resource hits it (the no-silent-caps rule). None of the current
  sources approach it.
- **Gating behavior change:** native read-only resources gate via
  `get_queryset` returning `.none()` for non-admins (the authored queries
  *raised* via `permission_classes`). An empty list vs a forbidden error is
  acceptable for an admin-only console (navigation already gates it) — but
  confirm per page; this also makes the surface correctly MCP-scoped.
- **Grants revoke** stays an authored single-id mutation; do not try to model it
  as a resource mutation.
- **Render-storm hazard** (`resource-view-surface.ts:561`): when wiring the
  client branch + list grouping, test grouping + opening the filter popover for
  the WebKit freeze the guard warns about.
- **`PlatformModel`/`PlatformField` ids:** the strawberry types lack an `id`;
  the pydantic rows MUST add a stable string `id` (label / `model.field`).

## Execution guidance (for the orchestrator)

1. **F1 → F2 first, by ONE agent, fully browser-verified.** This is the
   load-bearing primitive; everything composes onto it. Do not parallelize until
   the Addons page groups client-side in the running app.
2. Then fan out: **Stage B resources** (B1/B2/B3 are independent — parallel
   agents) and **Stage P pages** (each page independent — parallel agents, each
   mirroring the proven Addons conversion).
3. **Stage D** after all pages migrate (deletion is gated on zero importers).
4. **Stage V** last; then `/code-review` the diff and commit per repo
   conventions.

References: `docs/frontend/guidelines.md` (row-model rule, page-thinness, the
checks/drift-scan), `docs/stack.md` (TanStack Table owns client row models;
strawberry-django-hasura owns the Hasura dialect),
`.agents/plans/hasura-row-resource-non-model.md` (the backend foundation +
owner-split).
