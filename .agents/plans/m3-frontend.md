# M3 — Frontend lift

Bring the frontend onto the standard GraphQL contract M1/M2 now emit: relay
cursor connections, real GraphQL enums, `noteAggregate(groupBy:)`, verb-first
mutations, session login. Deliver login + full notes working end-to-end in a
browser. Reconstruct, never copy; no provenance in any artifact.

Reference (internal only — never in shipped code/docs/commits): the working
prototype `../angee-django-p1`. Consult it when stuck (standing principle in the
lift STATE), reconstruct natively to this repo's conventions.

## Locked decisions (architect)

- **Scope:** full M3 end-to-end, phased, with review checkpoints.
- **Pagination:** native keyset cursors for navigation (`after`/`before` +
  `startCursor`/`endCursor`/`hasNextPage`/`hasPreviousPage`). **`totalCount` is
  owned by the backend connection** (`NoteTypeCursorConnection.totalCount`) — read
  it from there; derive total pages = ceil(totalCount / pageSize) for display.
  Never synthesize counts or fabricate offset cursors client-side.
- **Live updates:** rework cache invalidation to per-model `<model>Changed`
  subscriptions on the **console** schema (`noteChanged: ChangeEvent!`), over the
  already-wired graphql-ws transport.

## The contract delta (SDK is geared to the old synthetic `Sale` schema)

Emitted truth: `examples/notes-angee/src/runtime/schemas/{public,console}.graphql`.

| Concern | Stale SDK assumption | Emitted contract |
|---|---|---|
| Node id | `id: Sqid!` | `id: ID!` (relay `Node`/`AngeeNode`) |
| List | `sales(search, order:[Order!], first, after)` | `notes(filters: NoteFilter, order: NoteOrder, first, last, before, after)` — no `search`, order is `@oneOf` |
| Order | `[Order!]` of String | `NoteOrder @oneOf { title/status/updatedAt/createdAt: Ordering }` (enum) |
| Filter | bare scalars | lookup objects (`NoteStatusFilterLookup`, `StrFilterLookup`, `BoolBaseFilterLookup`, `AND/OR/NOT/DISTINCT`) |
| Enums | `SaleState` only | `NoteStatus`, `Ordering`, `NoteGroupBy` |
| Mutations | `saleCreate(input:)`, `saleUpdate(id:,input:)`, `saleDelete(id:)` | `createNote(data: NoteInput!)`, `updateNote(data: NotePatch!)` (id inline), `deleteNote(id: ID!)` |
| DeletePreview | `{ ok id totalDeletedCount … }` | `{ totalDeletedCount deleted{label count} updated{…} blocked{…} hasBlockers }` |
| Aggregate | `salesAggregate` + `salesGroupBy` (sum/avg/min/max, `key`) | one `noteAggregate(groupBy:[NoteGroupBy!]) → { count, groups:[NoteGrouped{count,status,isStarred,updatedAtMonth}] }` — count-only, flat |
| Login | `login(input:)→{ok,error,user}` | `login(username,password)→LoginPayload{ok,user}` |
| Logout | `logout→{ok}` | `logout: Boolean!` |
| currentUser | `CurrentUser{roles,isSuperuser}` | `UserType{id,username,firstName,lastName,email,isStaff,isActive}` — no roles/superuser |
| Subscription | one `events` firehose (public) | per-model `noteChanged: ChangeEvent!` (console only); `ChangeEvent{model,id,action,changedFields,changedValues}` |

Already correct, keep: `cache-config.ts` (keys on `id`, wires `relayPagination()`
per `*Connection` — works for `ID!`; only its comment says "Sqid"),
`graphql-client.ts`/`graphql-provider.tsx` (urql + graphql-ws + CSRF + session +
per-schema clients), `document-query.ts`, `stable-deps.ts`, `make-context.ts`,
`relay-registry.ts`, `i18n.ts`, `define-addon.ts`.

## Phase 1 — SDK → new contract (task #1)

TDD: rewrite the contract assertions in the SDK tests to the emitted shapes first,
then make them green. The document-builder tests assert exact strings and need no
schema.

1. **`selection.ts`** — rebuild document assembly:
   - detail: `query note($id: ID!){ note(id:$id){…} }`.
   - list: `notes($first,$after,$last,$before, filters: NoteFilter, order: NoteOrder)`;
     select `totalCount edges{ cursor node{…} } pageInfo{ startCursor endCursor hasNextPage hasPreviousPage }`.
     Drop `search`. Order arg is the singular `@oneOf` input, not a list.
   - mutations verb-first: `createNote(data: NoteInput!)`, `updateNote(data: NotePatch!)`
     (no separate `$id`), `deleteNote(id: ID!)` selecting the new DeletePreview shape.
   - aggregate: one `noteAggregate(groupBy: [NoteGroupBy!])`; selection
     `count groups{ count <dimension fields> }`. Delete `groupByFieldName`,
     `assembleGroupByDocument`, `MEASURE_OPERATORS`, sum/avg/min/max.
     `aggregateFieldName` → `${singular}Aggregate` (`noteAggregate`), not plural.
   - **Delete** `pageToConnectionArgs` + `encodeOffsetCursor` (offset faking).
     Keep `RELAY_MAX_PAGE_SIZE`/`RELAY_PAGE_SIZE_OPTIONS`.
   - Input type names: create uses `${Type}Input`, update uses `${Type}Patch`.
2. **`resource-result.ts`** — `PageInfo = {startCursor,endCursor,hasNextPage,hasPreviousPage}`;
   keep `total` from `connection.totalCount` (backend-owned).
3. **`resource-hooks.ts`** — `useResourceList`: cursor paging (`first/after`,
   `last/before`); return `{rows, total, pageInfo, loadNext, loadPrev, refetch, …}`.
   The UI derives pages from `total`. `useResourceMutation`: create/update pass
   `data:` (update's `data` carries `id`); delete passes `id` and returns the
   DeletePreview (not a node). Invalidate on create/delete.
4. **`aggregates.ts` + `aggregate-extract.ts`** — collapse to the single
   `noteAggregate` shape. `useAggregateQuery` → `{count}` ungrouped.
   `useResourceGroupBy(model,{groupBy:[NoteGroupBy], dimensionFields})` →
   `{count, groups:[{count, …dims}]}`; bucket = `{key:{dim:value}, count}`. Drop
   measures/`selectMeasure`/`AggregateFn`.
5. **`auth.ts` + `auth-hooks.ts`** — UserType fields
   (`id username firstName lastName email isStaff isActive`); drop
   `roles`/`isSuperuser`/`hasRole` sourcing (role-gating deferred per F6 — client
   gates are UX-only, server authorizes). `login(username,password)→{ok,user}`;
   `logout: Boolean`.
6. **`relay-invalidation.tsx`** — drop the `events` firehose. Subscribe per
   registered model to `${singular}Changed` on the **console** client; match
   `ChangeEvent{model,id,action,changedFields,changedValues}`. Resolve the console
   client explicitly (urql context binds one schema; the invalidation listener
   needs the console client — wire it from `createSchemaClients`).
7. **codegen** — point `codegen.ts` + `bin/build-resource-types.mjs` at the emitted
   `runtime/schemas/public.graphql` (+ console). Scalars map: `DateTime`,`JSON`
   (drop `Sqid`,`Decimal`). **Flag for review:** the framework SDK reading the
   example's runtime path is a cross-level coupling; `__generated__` is a gitignored
   build artifact (SDK source never names `Note`), and the codegen comment already
   prescribes pointing at `runtime/schemas/<name>.graphql`. Confirm acceptable or
   move per-project codegen into the example web package.
8. Update every `*.test.ts(x)` to the new contract. Gate: `pnpm --filter @angee/sdk codegen && typecheck && test` green. Review checkpoint: react-reviewer.

## Phase 2 — @angee/base rendered binding (task #2)

New `packages/base` (`@angee/base`). Minimal rendered binding to render notes+auth
— not the full catalog. Per `docs/stack.md` frontend rows (React 19, TanStack
Router/Form/Table/Virtual, base-ui-components, tailwind-variants, tailwind 4,
lucide, valibot, i18next). `createApp` host composition + `defineAddon` already in
the SDK. Pieces: provider stack/app shell + chrome, login page + username/password
form, ListView (cursor pager that shows `total`/pages from `totalCount`, status
enum column + filter, order via `NoteOrder`), FormView (TanStack Form, enum
`select`), aggregate/group-by panel (count by status/isStarred/month). Reference p1
`packages/base` for the proven shape; reconstruct natively, less code.

## Phase 3 — notes + auth example web app (task #3)

`examples/notes-angee/src/example/notes/web` + host web: notes `defineAddon`
(routes home/detail/new, menu, widgets, i18n), auth login route, host `createApp`,
the list/detail/create/edit + aggregates UI, demo-credentials slot. Per-project
codegen from the emitted public SDL. Add the web globs to `pnpm-workspace.yaml`.

## Phase 4 — dev-serving (task #4)

Add a Vite service to `templates/stacks/dev/.../angee.yaml.jinja`; host serves the
SPA + GraphQL public/console (WS). CSRF (`/auth/csrf/`) + session cookie. `angee
dev` brings backend + frontend up together.

## Phase 5 — browser e2e + full gate (task #5)

Browser: login alice → her notes (relay-paged, total/pages correct) → create/edit/
delete → status enum filter → aggregate counts → live `noteChanged` update. Full
gate: `pnpm typecheck/test/build` + backend `ruff`/`mypy`/`pytest`/example e2e.

## Verify

Frontend per-area checks (`docs/frontend/guidelines.md`): `pnpm typecheck`, `pnpm
test`, `pnpm build`, browser verification. Backend gate unchanged. No provenance
anywhere in shipped artifacts.
