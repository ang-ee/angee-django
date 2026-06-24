# Refine Adoption — Hasura Execution Todo

Date: 2026-06-23
Source plan: [`refine-adoption-refactor-plan.md`](./refine-adoption-refactor-plan.md)

This is the working checklist for parallel execution. Keep the narrative plan for
architecture and rationale; keep this file for state.

## Update Protocol

- Check a box only after the named verification passes or the item explicitly
  says it is bookkeeping-only.
- When a parallel agent owns a lane, add `Owner: <name/thread>` under that lane.
- If a task blocks, leave it unchecked and add `Blocked:` with the exact missing
  dependency.
- If a task creates a follow-up, add it under the same lane instead of relying on
  private memory.

## Shared Rule

- Prefer the stock refine/Hasura/TanStack owner for every generic data concern.
  Angee work in this plan should be metadata, composition, authored custom
  operations, or a small adapter/library extension at the owner level. Do not add
  local provider dialects, local CRUD engines, or one-off grouped/list/form/table
  implementations.

## Baseline Done

- [x] Dialect settled on Hasura via A/B spike.
- [x] `strawberry-django-hasura` 0.2.0 built, verified, and published to PyPI.
- [x] `strawberry-django-aggregates` 0.7.0 published to PyPI.
- [x] `hasura_resource(...)` built and exported by `strawberry-django-hasura`.
- [x] Stock `@refinedev/hasura` proof green with `idType: "String"`.
- [x] Strawberry-Django fork pk lookup and mutation-copy fixes landed.
- [x] `django-zed-rebac` `for_write()` landed.
- [x] Angee `crud.py` simplified onto stock mutations plus `for_write()`.
- [x] Admin `permission_classes` / `write_context` redundancy removed; the
  flagged storage drive create rule is fixed and verified.
- [x] Operator daemon emits Hasura dialect, including NDC-shaped `_groups`, and
  is installed.

## Immediate Queue

- [x] Wire `strawberry-django-hasura` into `pyproject.toml`, `uv.lock`, and
  `docs/stack.md`.
- [x] Finish real-Angee `notes_hx` validation using `hasura_resource(...)`.
- [x] Fix stale wording in the narrative plan that says the Hasura lib exports
  only primitives.
- [x] Add the `storage/drive` `create` REBAC rule before storage migration.
- [x] Define the shared NDC-shaped grouped result contract for Django and
  operator grouped hooks.
- [x] Start Phase 1 metadata artifact.
- [x] Finish Phase 1 resource field/root metadata verification: explicit
  read/create/update/required/revision capability, no frontend SDL root/input
  inference, and focused backend/frontend gates green.
- [x] Start Phase 2 on notes: add the Angee `HasuraResource` metadata bridge,
  then replace notes `data_query(...)` / `crud(...)` with `hasura_resource(...)`.
- [x] Pass `groupable=[...]` through the Django `hasura_resource(...)` surface
  before closing the notes migration, because notes currently exposes
  `note_groups` and the replacement must be the adapter-owned NDC-shaped
  `notes_groups` root.

## Lane A — Dependency And Stack Wiring

- [x] Add backend dependency: `strawberry-django-hasura`.
- [x] Update `uv.lock`.
- [x] Add `docs/stack.md` backend row for `strawberry-django-hasura`.
- [x] Drop the local editable source override now that
  `strawberry-django-hasura>=0.2.0` resolves from PyPI.
- [x] Add frontend dependencies: `@refinedev/core`, `@refinedev/hasura`,
  `@refinedev/react-table`, `@refinedev/react-hook-form`, `graphql-request`,
  `react-hook-form`, `@hookform/resolvers`, `zod`, and `graphql-ws@5`.
- [x] Update `package.json` / `pnpm-lock.yaml`.
- [x] Update `docs/stack.md` frontend rows.
- [x] Decide and record `urql` transport-only vs `graphql-request` replacement:
  `graphql-request` is the refine/Hasura owner; `urql` is transitional for
  remaining authored-operation paths until SDK deletion.
- [x] Decide and record `valibot` vs `zod` for form resolvers: zod is the
  refine/react-hook-form resolver owner; TanStack Form is transitional until
  Phase 4 rebind.
- [x] Verification: backend dependency imports resolve.
- [x] Verification: frontend dependency imports resolve in TypeScript
  (`@angee/data`, `@angee/base`, `@angee/sdk`, `@angee/operator` typechecks;
  peer check clean).

## Lane B — Real-Angee Hasura Spike (`notes_hx`)

- Status: ✅ closed for notes. The POC validation is green, the mainline `Note`
  schema now uses `hasura_resource(...)`, and the old notes `data_query(...)` /
  `crud(...)` surface is deleted. Lane E owns repeating this per remaining model.

- [x] Architecture gate note: owner map for `notes_hx`.
- [x] Wire `hasura_resource(NoteType, name="notes", ...)` in the notes schema.
- [x] Supply REBAC-scoped read queryset.
- [x] Supply aggregate queryset using `scoped_for_aggregate()`.
- [x] Supply authorized write backend using `for_write()` and existing rebac
  write semantics.
- [x] Supply sqid `id_decode`.
- [x] Keep snake wire names pinned during mixed-schema migration.
- [x] Add/verify ungrouped `notes_aggregate { aggregate, nodes }`.
- [x] Add/verify grouped `notes_groups` root in the chosen NDC-shaped grouped
  contract.
- [x] Preserve `noteChanged` subscription behavior for live provider.
- [x] Replace old `data_query(...)` / `crud(...)` in main for notes.
- [x] Backend gate: `uv run examples/notes-angee/manage.py schema --check`.
- [x] Backend gate: notes Hasura list/detail/aggregate tests.
- [x] Backend gate: notes Hasura groups tests.
- [x] Backend gate: notes Hasura create/update/delete REBAC tests.
- [x] Backend gate: anonymous/non-owner denied or empty as appropriate.

## Lane C — Grouping Contract And Operator Alignment

Status: the cross-backend target shape has moved to the typed-key Hasura/NDC
preview contract. The Django adapter emits `group_by` specs and
`<res>_group { key, aggregate }` through optional `groupable=[...]` support,
using the free `<Model>Aggregate` and typed `<Model>GroupKey`. The old
`dimensions { key value }` / `aggregates` grouped result shape is hard-removed;
operator SDL/runtime and tests now track the typed-key contract. The duplicate
`groupableFieldEnum` and old wrapper `groupedResult` Angee metadata keys are
deleted; consumers use `groupBySpec` plus `groupDimensions[].input` instead.

Source audit (2026-06-24): `strawberry-django-hasura` owns optional
`groupable=[...]` support on `hasura_resource(...)`, emitting
`<res>_groups(group_by, where, having, order_by, limit, offset):
[<res>_group!]!`, where each group has `key: <Model>GroupKey!` and
`aggregate: <Model>Aggregate!`. It composes the public
`strawberry-django-aggregates` 0.7 typed-key seams and applies Hasura `_bool_exp`
through `where_to_q`; frontend authored operations now send `group_by` specs and
select typed key fields/range siblings from metadata.

- [x] Write the shared grouped result contract: dimensions, extraction,
  per-group aggregates, having/predicate, and pagination.
- [x] Map current `strawberry-django-aggregates` group spec to that contract.
- [x] Shape Django `<model>_groups` roots to the contract by passing
  `groupable=[...]` on each migrated resource.
  Adapter support is done in `strawberry-django-hasura`; first migrated model
  (`Note`) now uses it and has deleted the old grouped root. Keep applying this
  per later model migrations.
- [x] Verify operator `<res>_groups` roots use the same contract.
- [x] Delete duplicate `typeNames.groupableFieldEnum` metadata after Django and
  operator both moved to typed `group_by` specs; delete old
  `typeNames.groupedResult` wrapper metadata from the same surface.
- [x] Update facet expectations for the Hasura grouped wire shape. Scalar and
  relation facets now expect `@angee/data` specs (`dimensions`, per-facet
  `where`) instead of SDK `groups` / global-filter specs; aggregate extractor
  coverage remains with `@angee/data` operation tests.
- [x] Add cross-backend fixture tests for one Django model and one operator
  aggregate.
  Gate: `uv run python -m pytest tests/test_grouping_contract.py -q`.
- [x] Document the Hasura v3/DDN/NDC future-compatibility rule in the owner doc.
  Owner doc: `docs/stack.md` (`Hasura Dialect Rule`).

## Lane D — Phase 1 Metadata Artifact

- Status: ✅ DONE. Resource artifact now emits as `angee.resources`, is owned by
  `angee.graphql`, merges data-query/crud/revisions/changes contributions, and
  the frontend metadata owner consumes `resources` instead of `dataQueries`.
  Verified Phase 1 slices: writable/revision field capability; `groupOrder`;
  backend-owned group dimensions; aggregate/default measures; and backend
  default sort from exposed `Meta.ordering` terms. Frontend SDL root/input
  inference is deleted, grouping/facet builders consume `groupDimensions`, SDK
  validation rejects non-sortable defaults, backend metadata emission rejects
  unknown/to-many group axes plus unknown aggregate measures, and `@angee/base`
  list surfaces consume metadata `defaultSort` as their default resource order
  when URL sort and explicit `order` are absent. Default view/page-state defaults
  are settled at the page/view-state owner: `defaultView` is declarative, route
  serialization compares against page defaults, and default page size no longer
  leaks into the URL when another default writes search state. To-many node fields
  are now emitted as `list` fields instead of fake to-one relations, with relation
  lists carrying no scalar or many2one widget claim. Duplicate metadata names are
  rejected at the backend emission owner for data-query declarations, resource
  writable/revision declarations, and explicit/generated resource fields. Group
  axes and aggregate measures now reject unsupported to-many field paths even when
  the to-many relation is an intermediate path segment. Generated resource field
  scalars now come from the Strawberry surface, so public `id` emits as `ID` and
  model-path group dimensions reject unsupported Django field classes instead of
  guessing `String`. Computed Strawberry enum fields classify as `enum`,
  computed object/forward-reference fields classify as `relation`, and unsupported
  custom scalar fields fail fast instead of emitting scalar-less scalar metadata.
  Explicit resource field metadata is validated against the generated artifact
  vocabulary, and non-PK aggregate fields must map to supported frontend measure
  families. Filter/order/aggregate mechanics remain with strawberry-django and
  strawberry-django-aggregates; metadata only names, validates, and serializes
  their resulting facts. Phase 1 closure gates passed on 2026-06-23: 96 focused
  backend tests, `@angee/sdk` tests/typecheck, `@angee/base` tests/typecheck,
  notes metadata test, and `schema --check`.

- [x] Architecture gate note: metadata owners and sibling inventory.
- [x] Define artifact shape in `angee.graphql`, not the composer.
- [x] Include schema/resource roots: list, detail/by-pk, aggregate, groups, mutations,
  subscriptions.
- [x] Include field metadata: kind, scalar type, widget, explicit
  read/filter/sort and create/update/required-on-create capability.
- [x] Include root-owned writable/revision lists: `createFields`,
  `updateFields`, `requiredCreateFields`, `revisionFields`.
- [x] Include relation axes: identity field, label field, filter key, group key.
- [x] Include group axes and NDC grouping dimensions in `angee.resources`
  metadata.
- [x] Consume metadata-backed group dimensions in frontend grouping/facet query
  builders.
- [x] Include aggregate measures and default measures in `angee.resources`
  metadata.
- [x] Include backend-owned default sort from model ordering in
  `angee.resources` metadata.
- [x] Consume backend-owned `defaultSort` at the page/view-state owner as the
  default resource order for model-backed list surfaces.
- [x] Include/settle default view and remaining page/view-state defaults at
  their owning page/view-state primitive.
- [x] Classify to-many node fields as `list` in resource field metadata instead
  of relation.
- [x] Include provider/schema name (`public`, `console`, `operator`) where needed.
- [x] Emit artifact for notes.
- [x] Add snapshot test for notes artifact.
- [x] Add frontend tests proving roots, writable fields, required create fields,
  and revision fields come from `angee.resources`, not SDL heuristics.
- [x] Regenerate SDL and run `schema --check` after metadata contract changes.
- [x] Add fail-fast validation for duplicate metadata names.
- [x] Reject unsupported to-many group-axis and aggregate-measure field paths.
- [x] Source resource field scalars from Strawberry surfaces and reject unsupported
  model-path scalar classes.
- [x] Classify computed Strawberry enum/object fields and reject unsupported custom
  scalar resource fields.
- [x] Add fail-fast validation for remaining unsupported field/axis classes.
- [x] Keep filter/order/aggregate mechanics in their owners; metadata emits and
  validates facts only.
- [x] Delete frontend heuristic root/input-field inference only after consumers
  use the artifact and tests/typechecks are green.

  Note: duplicate model/node metadata validation, duplicate declaration/resource
  field validation, missing `groupOrder` type validation, group-key field
  validation, non-sortable default-sort validation, backend unknown/to-many
  group-axis plus unknown/to-many aggregate-measure path validation, unsupported
  to-many path traversal rejection, Strawberry-owned resource scalar
  classification, computed enum/object classification, unsupported model-path
  scalar-class rejection, unsupported custom-scalar rejection, explicit resource
  field vocabulary validation, unsupported aggregate-measure class rejection, and
  to-many node-field list classification are in place.

## Lane E — Phase 2 Backend Resource Migration

Current source audit (2026-06-23): mainline `Note`, IAM `User`/`Group`,
Storage `Drive`/`Folder`/`File`/`Backend`, Knowledge `Vault`/`Page`, all current
Integrate resources (`OAuthClient`/`ExternalAccount`/`Credential`/`Vendor`/
`Integration`/`WebhookSubscription`/`VcsBridge`/`Repository`/`Source`/
`Template`), and all current Agents resources (`Agent`/`Skill`/`MCPServer`/
`MCPTool`/`InferenceProvider`/`InferenceModel`), and Parties resources
(`Party`/`Person`/`Organization`/`Handle`/`Address`/`Affiliation`/`Folder`/
`Directory`), and Messaging resources (`Channel`/`Message`/`Thread`) now use
`hasura_resource(...)`. Repo-wide search confirms no addon schema still calls
`data_query(...)` or generic `crud(...)`; remaining occurrences are the core
compatibility primitives and tests.

Owner prep:

- [x] Add the Angee-owned `HasuraResource` -> `angee.resources` metadata bridge
  in `angee.graphql` before the first model migration.
- [x] Keep generic Hasura dialect mechanics in `strawberry-django-hasura`; the
  bridge should only attach Angee metadata, custom-root metadata, and schema
  bucket composition facts.
- [x] Add focused bridge tests proving list/by-pk/aggregate/mutation roots and
  input/type names land in `angee.resources`.

Per model checklist:

- [x] Replace old `data_query(...)` with `hasura_resource(...)`.
- [x] Replace old `crud(...)` with Hasura insert/update/delete roots.
- [x] Keep custom actions as authored operations, not generic resource CRUD.
- [x] Preserve `changes(...)` subscription roots.
- [x] Preserve `revisions(...)` roots where present.
- [x] Keep delete preview/custom delete-planning as an authored/custom operation
  rather than resource CRUD.
- [x] For resources that exposed groups, replace the old grouped root with the
  operator/NDC-shaped `<model>_groups` root in the same slice.
- [x] Verify snake names for node fields, args, inputs, and aggregate fields.
- [x] Verify `id` output is `ID`, pk args are `String`, and sqid decode works.
- [x] Verify read REBAC, aggregate REBAC, write REBAC.
- [x] Update metadata snapshot tests for the migrated resource.
- [x] Run `schema --check` for the migrated Storage slice.
- [x] Run targeted backend tests for the migrated Storage slice.
- [x] Run schema/backend/frontend/codegen gates for the migrated Knowledge slice.
- [x] Run schema/backend/frontend/codegen gates for the Integrate
      connection-substrate slice.
- [x] Run schema/backend/frontend/codegen gates for the full Integrate resource
      slice.
- [x] Run schema/backend/frontend/codegen gates for the Agents resource slice.
- [x] Run schema/backend/frontend/codegen gates for the Parties resource slice.
- [x] Run schema/backend/frontend/codegen gates for the Messaging resource slice.

### Notes

- [x] `Note`.

### IAM

- [x] `User`.
- [x] `Group`.

### Storage

- [x] Add `storage/drive` create rule and sync REBAC.
- [x] `Drive`.
- [x] `Folder`.
- [x] `File`.
- [x] `Backend`.

### Integrate

- [x] `OAuthClient`.
- [x] `ExternalAccount`.
- [x] `Credential`.
- [x] `Vendor`.
- [x] `Integration`.
- [x] `WebhookSubscription`.
- [x] `VcsBridge`.
- [x] `Repository`.
- [x] `Source`.
- [x] `Template`.

### Agents

- [x] `Agent`.
- [x] `Skill`.
- [x] `MCPServer`.
- [x] `MCPTool`.
- [x] `InferenceProvider`.
- [x] `InferenceModel`.

Agents slice note (2026-06-23): generic scalar/FK/M2M reads, groups, aggregate,
insert, update, and delete now ride `hasura_resource(...)`. Custom provider
create/update/connect and runtime provisioning/chat actions remain authored
domain operations. Agent many-to-many relation-array writes (`skills`,
`mcp_servers`, `mcp_tools`) are standard resource writes after adding generic
M2M writable-field support to `strawberry-django-hasura`.

### Parties

- [x] `Party`.
- [x] `Person`.
- [x] `Organization`.
- [x] `Handle`.
- [x] `Address`.
- [x] `Affiliation`.
- [x] `Folder` / `contact_folders`.
- [x] `Directory`.

Parties slice note (2026-06-23): generic reads, groups, aggregates, inserts,
updates, and deletes now ride `hasura_resource(...)` for the contact resources.
The CardDAV connect mutation stays authored because it transactionally creates a
credential, vendor, and directory and probes the backend. The shared
`strawberry-django-hasura` snake-name pinning helper now tracks visited
Strawberry object definitions so cyclic resource graphs such as
`Party -> Handle -> Party` terminate at the adapter owner.

### Knowledge

- [x] `Vault`.
- [x] `Page`.

### Messaging

- [x] `Channel`.
- [x] `Message`.
- [x] `Thread`.

Messaging slice note (2026-06-23): channel reads plus message/thread reads,
groups, aggregate, update, and delete now ride `hasura_resource(...)`. Message
creation remains manager/backend-owned ingest, so `Message` and `Thread` expose
no generic insert roots. Parts, participants, edges, reactions, and metrics stay
nested read projections reached through their owning message/thread. Live
`messageChanged` / `threadChanged` subscriptions remain authored roots.

## Lane F — Phase 3 Frontend Foundation (`@angee/data`)

- [x] Create/refactor `@angee/data` package surface.
- [x] Configure stock `@refinedev/hasura` provider with
  `namingConvention: "hasura-default"` and `idType: "String"`.
- [x] Add named providers: `public`, `console`, `operator` (provider map
  returns refine's required `{ default, ...named }` shape; `createApp` mounts
  the map using `defaultSchema`).
- [x] Normalize generated `*.metadata.json` at the shared app boundary before
  SDK/refine consumers see it (`defineAngeeSchemaMetadata` validates generated
  resource shape and narrows JSON-widened field kinds).
- [x] Implement authored-operation helper path using `meta.gqlQuery` /
  `meta.gqlMutation`
  (`aggregateRequest`, `groupByRequest`, and `facetsRequest` return
  refine-native `{ dataProviderName, meta: { gqlQuery, gqlVariables } }` requests
  over the stock Hasura provider; `deletePreviewRequest` returns
  `{ dataProviderName, meta: { gqlMutation, gqlVariables } }` for authored
  cascade-preview mutations).
- [x] Implement `liveProvider` over `graphql-ws` and existing `<model>Changed`
  subscriptions.
- [x] Implement `useAggregate` over `<model>_aggregate`.
- [x] Implement `useGroupBy` over `<model>_groups`.
- [x] Implement `useFacets` as aliased grouped requests.
- [x] Rebind scalar and relation list facets to `@angee/data` `useAngeeFacets`.
  Base now builds Hasura facet dimensions from resource metadata, computes
  neutralized per-facet `where` through `hasuraWhereFromAngeeFilter`, and
  synthesizes toolbar filters from facet declarations instead of relying on an
  SDK filter echo.
- [x] Rebind grouped-list bucket drill-down to the shared refine/Hasura path.
  `GroupedList` now consumes NDC `_groups` buckets from `@angee/data`,
  synthesizes scalar/relation/date-granular leaf filters from resource
  metadata, maps date buckets to `[gte, lt)` ranges, and routes child group
  `where` through `hasuraWhereFromAngeeFilter`. JSON bucket drill-down emits the
  owner-level `jsonContains` lookup and rides the same `@angee/data` Hasura bool
  expression path as groups/facets.
- [x] Repair stale/invalid grouped route state against resource metadata before
  rendering grouped queries. `ListView` now derives an effective group stack
  through the shared group-option owner, falls back to the legal default group
  when old URL state such as `updatedAt:day` is no longer a resource dimension,
  and writes the corrected snake-case group back to route state.
- [x] Implement `useDeletePreview` as Angee custom operation.
  Backend metadata now carries a separate `roots.deletePreview` fact for
  authored cascade-preview mutations (`delete_note`, `delete_user`,
  `delete_vault`, `delete_page`, `delete_external_account`,
  `delete_credential`). `roots.delete` remains the generic Hasura
  `delete_<resource>_by_pk` root.
- [x] Implement `useResourceList` over refine `useList` + stock
  `@refinedev/hasura`.
  The hook derives the refine resource/provider from `angee.resources`, runs
  through refine `useList` and the stock Hasura provider, and passes an
  owner-authored `listRequest` through `meta.gqlQuery` / `meta.gqlVariables` so
  field selection, Hasura `where`, and `order_by` all use the same resource
  metadata and bool-expression contract as groups/facets. Pagination, query
  keys, invalidation, and live refresh remain owned by refine.
- [x] Move shared `@angee/base` model list callers to the refine-backed
  `@angee/data` hook: `data-view-surface`, grouped leaf rows, related rows,
  relation options, and record-navigation sync.
- [x] Delete the old SDK `useResourceList` implementation, exports, and
  list-only tests after the shared base callers moved.
- [x] Implement first metadata loader from Phase 1 artifact
  (`angee.resources` -> refine resource names, provider names, route paths).
- [x] Feed refine `resources` from metadata in `createApp`.
- [x] Add TanStack Router `routerProvider` and mount `<Refine>` inside the root
  TanStack route so refine router hooks run under router context.
- [x] Gate: frontend foundation package checks passed on 2026-06-23:
  `@angee/sdk` typecheck + model-metadata tests, `@angee/data` typecheck/tests,
  `@angee/base` typecheck/tests, `@angee-example/notes-host` typecheck/build,
  and `pnpm peers check`.
- [x] Gate: custom Hasura operation helpers passed on 2026-06-23:
  `pnpm --filter @angee/data typecheck` and `pnpm --filter @angee/data test`
  with aggregate/group/facet document, variable, and extractor coverage.
- [x] Gate: delete-preview metadata/hook slice passed on 2026-06-23:
  `uv run examples/notes-angee/manage.py schema --check`,
  `uv run python -m pytest tests/test_crud.py tests/test_aggregates.py -q`,
  `uv run python -m pytest tests/test_iam_graphql.py -q`,
  `uv run python -m pytest tests/test_integrate_graphql.py -q`,
  `uv run python -m pytest tests/test_knowledge_graphql.py -q`,
  `uv run examples/notes-angee/manage.py test example.notes.tests.test_schema_metadata -v 2`,
  `pnpm --filter @angee/data typecheck`, `pnpm --filter @angee/data test`,
  `pnpm --filter @angee/sdk typecheck`, `pnpm --filter @angee/sdk test`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`, and
  `pnpm peers check`.
- [x] Gate: refine liveProvider slice passed on 2026-06-23:
  `pnpm --filter @angee/data typecheck`, `pnpm --filter @angee/data test`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base test -- createApp`, and
  `pnpm --filter @angee-example/notes-host typecheck`, and
  `pnpm peers check`.
- [x] Gate: refine list-hook slice passed on 2026-06-23:
  `pnpm --filter @angee/data typecheck`, `pnpm --filter @angee/data test`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk test -- resource-hooks resource-invalidation`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base test -- DataPage RelatedRowsList DeleteBulkFlow`,
  `pnpm --filter @angee-example/notes-host typecheck`, and
  `pnpm peers check`.
- [x] Gate: grouped-list Hasura/NDC drill-down slice passed on 2026-06-23:
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/group-dimension.test.ts src/views/DataPage.test.tsx`,
  `pnpm --filter @angee/base test`, `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/data test`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee-example/notes-e2e exec playwright test tests/notes.spec.ts -g "sees her scoped notes"`,
  and `pnpm --filter @angee-example/notes-e2e exec playwright test tests/notes-views.spec.ts`.
- [x] Gate: stale group-search repair passed on 2026-06-23:
  `pnpm --filter @angee/base exec vitest run src/views/DataPage.test.tsx --testNamePattern "repairs stale camel-case group search"`,
  `pnpm --filter @angee/base exec vitest run src/views/DataPage.test.tsx`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/group-dimension.test.ts src/views/DataPage.test.tsx`,
  and `pnpm --filter @angee-example/notes-e2e exec playwright test tests/notes-views.spec.ts -g "date buckets drill down"`.
- [x] Gate: facet migration and SDK facet deletion passed on 2026-06-23:
  `pnpm --filter @angee/base exec vitest run src/views/scalar-facet.test.tsx src/views/relation-facet.test.tsx`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts src/list.test.ts`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/sdk typecheck`, and
  `pnpm --filter @angee/sdk test -- graphql-source`.
- [x] Gate: aggregate/group SDK deletion passed on 2026-06-23:
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk test -- selection stable-deps`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/group-dimension.test.ts src/views/scalar-facet.test.tsx src/views/relation-facet.test.tsx`,
  `pnpm --filter @angee/data typecheck`, and
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts src/list.test.ts`.
- [x] Gate: record/revision hooks moved from SDK to `@angee/data` passed on
  2026-06-23: `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk exec vitest run src/resource-hooks.test.tsx src/resource-result.test.ts src/selection.documents.test.ts`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts src/list.test.ts`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/communication/RevisionsTab.test.tsx src/views/FormView.test.tsx src/views/RelationPicker.test.tsx src/views/DataPage.test.tsx src/views/DataPage.routed.test.tsx`,
  `pnpm --filter @angee/agents typecheck`,
  `pnpm --filter @angee/storage typecheck`,
  `pnpm --filter @angee/knowledge typecheck`, and
  `pnpm --filter @angee-example/notes-web typecheck`.
- [x] Gate: mutation hook moved from SDK to `@angee/data` and stale
  camel/snake group search repair rechecked on 2026-06-23:
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/group-dimension.test.ts src/views/FormView.test.tsx src/views/RelationPicker.test.tsx src/views/DataPage.test.tsx src/views/DataPage.routed.test.tsx src/views/DeleteBulkFlow.test.tsx`,
  `pnpm --filter @angee/sdk exec vitest run src/resource-result.test.ts src/selection.test.ts src/selection.documents.test.ts`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts src/list.test.ts src/resources.test.ts`,
  `pnpm --filter @angee/storage typecheck`,
  `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee/storage exec vitest run src/data/actions.test.tsx`,
  and
  `pnpm --filter @angee/knowledge exec vitest run src/data/use-page-actions.test.tsx src/data/use-page-editor.test.tsx`.
- [x] Add owner-level JSON containment drill-down.
  `strawberry-django-hasura` now exposes `JSON_comparison_exp._contains` and
  maps it to `JSONField__contains`; `@angee/data` owns the `jsonContains` ->
  Hasura `_contains` translation and list reads use the authored
  `meta.gqlQuery` path because stock `@refinedev/hasura` `CrudFilters` do not
  expose JSON containment.
- [x] Gate: JSON containment drill-down slice passed on 2026-06-23:
  sibling `strawberry-django-hasura`:
  `uv run pytest tests/test_filtering.py tests/test_sdl_contract.py` and
  `uv run pytest tests/test_crud.py`; Angee frontend/data:
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/data exec vitest run src/list.test.ts src/operations.test.ts`,
  `pnpm --filter @angee/base exec vitest run src/views/group-dimension.test.ts`,
  `pnpm --filter @angee/base exec vitest run src/views/DataPage.test.tsx src/views/DataPage.routed.test.tsx`,
  `pnpm --filter @angee/base exec vitest run src/views/RowsListView.test.tsx src/views/AuthoredRowsList.test.tsx src/views/RelatedRowsList.test.tsx src/views/DeleteBulkFlow.test.tsx`,
  and
  `pnpm --filter @angee/data exec vitest run src/resources.test.ts src/provider.test.ts`.
- [ ] Add owner-level grouped bucket ordering if the product needs sorted
  buckets under the Hasura/operator contract; the current `_groups` roots expose
  no `order_by`, so list sorting applies to leaf records inside expanded groups.
- [x] Gate: one model-backed list renders through refine against real backend.
  Gate: `pnpm --filter @angee-example/notes-e2e exec playwright test
  tests/notes.spec.ts -g "sees her scoped notes"` against the running local
  stack with local-network access.
- [ ] Gate: repo-wide `pnpm run typecheck && pnpm run test && pnpm run build`
  if still required after the first refine-rendered list.

## Lane G — Phase 3b Cross-Cutting Providers

- [x] Repair the mixed auth wire contract exposed by the real-backend render
  gate: `CurrentUserType` now uses Hasura-default snake_case like `UserType`,
  while `@angee/data` aliases those fields back to the public camel-case
  `AuthUser` shape.
  Gates: `uv run examples/notes-angee/manage.py schema --check`,
  `uv run python -m pytest tests/test_iam_permission_hub_graphql.py
  tests/test_iam_graphql.py -q`,
  `uv run examples/notes-angee/manage.py test
  example.notes.tests.test_iam_graphql -v 2`,
  `pnpm --filter @angee/data test`, `pnpm --filter @angee/sdk typecheck`,
  and `pnpm --filter @angee/base typecheck`.
- [x] Implement refine `authProvider`.
  `@angee/data` now owns `createAngeeAuthProvider(...)` over the Hasura session
  GraphQL roots, and `createApp` passes it to `<Refine>`.
- [x] Implement identity lookup.
  Refine `getIdentity` reads `currentUser`, maps `role_refs` into
  `AuthUser.roles`, and base auth state reads through Refine identity.
- [x] Implement login.
  The login form now calls the `@angee/data` wrapper over Refine `useLogin`.
- [x] Implement logout with cache clearing.
  Logout runs through Refine `useLogout`; successful login/logout call
  `onAuthChange`, which resets the remaining urql client pool while Refine
  invalidates auth queries.
- [x] Implement preferences mutation.
  Preferences now use a Refine custom Hasura mutation request and invalidate the
  Refine auth store after writes.
- [x] Implement `i18nProvider`.
  `@angee/data` now owns `createAngeeI18nProvider(...)`, and `createApp` passes
  merged runtime bundles to Refine.
- [x] Preserve namespace fallback / bundled-English fallback.
  The Refine i18n provider resolves `namespace.key`, explicit `namespace`
  options, default messages, and key fallback; existing `useNamespaceT` fallback
  remains the runtime owner until Lane I.
- [x] Gate: auth tests.
  Gates: `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/data test`, `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/auth/LoginPage.test.tsx
  src/shell/ConsoleShell.smoke.test.tsx src/chrome/app-rail-preferences.test.ts
  src/createApp.test.ts`, `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk test`, `pnpm --filter @angee-example/notes-host
  typecheck`, `pnpm --filter @angee-example/notes-web typecheck`, `pnpm peers
  check`, and `pnpm --filter @angee-example/notes-e2e exec playwright test
  tests/notes.spec.ts -g "sees her scoped notes"`.
- [x] Gate: preferences tests.
  Covered by `packages/data/src/auth.test.ts` preference request assertions and
  the focused base preference shell tests above.
- [x] Gate: i18n fallback tests.
  Covered by `packages/data/src/i18n.test.ts`.

## Lane H — Phase 4 `@angee/base`

- [ ] Rebind `ListView` / `RowsListView` / `DataPage` on
  `@refinedev/react-table`.
- [ ] Preserve grouping.
- [ ] Preserve board view.
- [ ] Preserve selection.
- [ ] Preserve keyboard and toolbar affordances.
- [x] Move board/grouped/favorites view-state to `@angee/base`.
- [x] Delete sort/filter/paginate overlap from `data/view-state.ts`.
  SDK `data/view-state.ts`, `data/local-source.ts`, and `data/query.ts` are
  deleted; base now owns `views/data-view-model.ts` and `views/local-rows.ts`.
  Gates: `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/data-view-model.test.ts
  src/views/local-rows.test.ts src/createApp.test.ts src/views/DataPage.test.tsx
  src/views/DataPage.routed.test.tsx src/views/RowsListView.test.tsx
  src/views/ListView.tsx src/views/group-dimension.test.ts`, `pnpm --filter
  @angee/sdk test`, and `pnpm --filter @angee/data typecheck`.
- [ ] Rebind `FormView` on `@refinedev/react-hook-form`.
- [ ] Choose and wire resolver (`zodResolver` unless valibot is retained).
- [ ] Preserve `<Field>` / `<Group>` / `showWhen` DSL.
- [ ] Rebind `RecordView` / detail surfaces.
- [ ] Gate: base vitest.
- [ ] Gate: storybook.
- [ ] Gate: model-backed `DataPage` end-to-end.

## Lane I — Phase 5 `@angee/runtime`

- [ ] Define `@angee/runtime` package boundary.
- [ ] Move/thin `defineAddon`.
- [ ] Move/thin `composeAddons`.
- [ ] Keep route-target validation owned by runtime.
- [ ] Keep chrome, breadcrumbs, shell owned by runtime.
- [ ] Keep model-route indexing owned by runtime.
- [ ] Derive refine `resources` as a projection.
- [ ] Prove equivalent addon composition with full addon set.
- [ ] Delete only menu/route registry code genuinely subsumed by refine
  resources.
- [ ] Gate: `addon-composition.test.tsx`.

## Lane J — Phase 6 Addon Frontend Migration

Per addon checklist:

- [ ] Switch pages/resources to refine hooks.
- [ ] Switch authored operations to codegen `graphql()` documents.
- [ ] Route authored docs to the correct schema (`public`, `console`,
  `operator`).
- [ ] Replace bespoke `useAuthored*` paths.
- [ ] Replace bespoke action hooks with `useCustomMutation` or thin
  `@angee/data` helper.
- [ ] Delete `ctx.record -> mutate -> refresh` paths.
- [ ] Run package typecheck/test/build.
- [ ] Run addon e2e where available.

Addon order:

- [ ] Notes.
- [ ] IAM.
- [ ] Storage.
- [ ] Integrate.
- [ ] Agents.
- [ ] Parties.
- [ ] Knowledge.
- [ ] Messaging.
- [ ] Operator console.

## Lane K — SDK Deletion

- [x] Delete `resource-hooks.ts` list hook/export/tests after moving list reads
  to `@angee/data`.
- [x] Move/delete `resource-hooks.ts` record/revision hooks, exports, tests, and
  orphaned read document/result helpers after rebinding callers to
  `@angee/data`.
- [x] Replace remaining `resource-hooks.ts` mutation hook.
- [ ] Delete `authored-hooks.ts`.
- [ ] Delete `action-hooks.ts` and `action-result.ts`, after replacement.
- [ ] Delete `document-query.ts`, `document-mutation.ts`,
  `document-subscription.ts`, and `disabled-documents.ts`, after replacement.
- [ ] Delete `stable-deps.ts`.
- [ ] Delete `graphql-client.ts`, `graphql-provider.tsx`, `cache-config.ts`,
  and `schema-object-types.ts`.
- [ ] Delete `relay-invalidation.tsx` and `relay-registry.ts`.
- [ ] Delete `selection.ts` after codegen `TypedDocumentNode` path is complete.
- [x] Delete heuristic root-field/input-shape inference after metadata artifact
  is used.
- [ ] Move/thin the remaining metadata loader/validation from
  `model-metadata.tsx` into the final `@angee/data` owner.
- [ ] Keep only thin field/widget classification that still belongs in
  `@angee/data`.
- [x] Delete sort/filter/paginate slice of `data/view-state.ts`.
- [x] Move board/grouped/favorites state to `@angee/base`.
- [x] Delete `data/graphql-source.ts`; aggregate/group/facet authored requests
  now live in `@angee/data` operation helpers.
- [x] Delete SDK `data/local-source.ts`, `data/query.ts`, and `data/index.ts`;
  local-row querying now lives beside the rendered base list surface.
- [x] Move/reuse `data/facets.ts` logic in `@angee/data`; scalar/relation
  facets now call `useAngeeFacets`, and neutralized counts are translated
  through the shared base `facet-query` helper.
- [x] Delete the old SDK `data/facets.ts` implementation, tests, exports, and
  orphaned facet document assembler.
- [x] Move/reuse `aggregates.ts` / `aggregate-extract.ts` logic in
  `@angee/data`; the duplicate SDK aggregate hooks, extractors, tests, exports,
  and old aggregate/group selection builders are deleted.
- [x] Delete SDK `auth.ts`, `auth-hooks.ts`, `preferences.tsx`, their exports,
  and their tests after Phase 3b auth moved to `@angee/data`.
- [x] Move/delete SDK `error-message.ts` and `use-busy-run.ts`.
  `@angee/base` feedback now owns user-facing error message fallback and
  rendered async action busy state; SDK exports, files, and tests are deleted.
  `document-mutation.ts` keeps its submitted-promise tracking locally until the
  authored document mutation layer is deleted.
  Gates: `pnpm --filter @angee/base exec vitest run
  src/feedback/error-message.test.ts src/feedback/use-busy-run.test.tsx
  src/auth/LoginPage.test.tsx src/auth/OAuthCallback.test.tsx
  src/views/record-action.test.tsx src/views/DeleteBulkFlow.test.tsx`,
  `pnpm --filter @angee/sdk exec vitest run src/document-mutation.test.tsx`,
  `pnpm --filter @angee/base typecheck`, `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/storage exec vitest run src/data/actions.test.tsx`,
  `pnpm --filter @angee/knowledge exec vitest run
  src/data/use-page-actions.test.tsx`, `pnpm --filter @angee/storage
  typecheck`, `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee/sdk test`, and `pnpm peers check`.
- [x] Move/delete SDK `resource-result.ts`.
  `@angee/data` now owns `Row`, `PageInfo`, `PageResult`, and `rowPublicId`;
  `@angee/base` re-exports them for addon UI code. SDK graphcache uses a private
  node id reader, authored row hooks use their own string-id row type, and the
  old SDK file/test/export are deleted.
  Gates: `pnpm --filter @angee/data exec vitest run src/rows.test.ts
  src/list.test.ts src/resources.test.ts src/operations.test.ts
  src/provider.test.ts`, `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/sdk typecheck`, `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/sdk exec vitest run src/cache-config.test.ts
  src/authored-hooks.test.tsx`, `pnpm --filter @angee/base exec vitest run
  src/views/DataPage.test.tsx src/views/DataPage.routed.test.tsx
  src/views/FormView.test.tsx src/views/RelationPicker.test.tsx
  src/views/model-metadata-defaults.test.ts`, `pnpm --filter @angee/sdk test`,
  `pnpm --filter @angee/agents typecheck`, `pnpm --filter @angee/integrate
  typecheck`, `pnpm --filter @angee/knowledge typecheck`, `pnpm --filter
  @angee/iam typecheck`, `pnpm --filter @angee-example/notes-web typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`, and `pnpm peers check`.
- [x] Move/delete SDK `resource-types.ts`.
  `@angee/data` now owns the open `ResourceTypeMap` contract and resource
  filter/order generic types used by refine-backed hooks; SDK file/test/export
  are deleted.
  Gates: `pnpm --filter @angee/data exec vitest run
  src/resource-types.test.ts src/rows.test.ts src/list.test.ts`,
  `pnpm --filter @angee/data typecheck`, `pnpm --filter @angee/base
  typecheck`, `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk test`, `pnpm --filter @angee/base exec vitest run
  src/views/DataPage.test.tsx src/views/DataPage.routed.test.tsx
  src/views/RelatedRowsList.test.tsx src/views/RowsListView.test.tsx`,
  `pnpm --filter @angee-example/notes-host typecheck`, `pnpm --filter
  @angee-example/notes-web typecheck`, and `pnpm peers check`.
- [x] Move/delete SDK `validation-errors.ts`.
  `@angee/base` form view now owns GraphQL validation-extension extraction and
  field/form message binding; SDK file/test/export are deleted.
  Gates: `pnpm --filter @angee/base exec vitest run
  src/views/validation-errors.test.ts src/views/FormView.test.tsx`,
  `pnpm --filter @angee/base typecheck`, `pnpm --filter @angee/sdk
  typecheck`, and `pnpm --filter @angee/sdk test`.
- [x] Thin SDK `selection.ts`.
  `@angee/data` now owns GraphQL selection tree building/printing and page-size
  clamp tests for Hasura operation documents; `@angee/data` rows own
  `publicIdLabel`. SDK `selection.ts` keeps only `typeNameForModel` for runtime
  metadata/invalidation until those owners move.
  Gates: `pnpm --filter @angee/data exec vitest run src/selection.test.ts
  src/rows.test.ts src/operations.test.ts src/list.test.ts`,
  `pnpm --filter @angee/data typecheck`, `pnpm --filter @angee/base exec
  vitest run src/views/FormView.test.tsx src/views/data-view-model.test.ts
  src/views/local-rows.test.ts`, `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/sdk exec vitest run src/selection.test.ts
  src/model-metadata.test.tsx src/relay-invalidation.test.tsx`,
  `pnpm --filter @angee/sdk typecheck`, `pnpm --filter @angee/sdk test`,
  `pnpm --filter @angee-example/notes-host typecheck`, `pnpm --filter
  @angee-example/notes-web typecheck`, and `pnpm peers check`.
- [ ] Move/delete SDK `i18n.ts` after the runtime/addon-manifest owner leaves
  `@angee/sdk`; it still backs `runtime.ts`, `define-addon.ts`, and addon
  `useNamespaceT` helpers until Lane I.
- [ ] Move/thin `runtime.ts` / `define-addon.ts` into `@angee/runtime`.
- [ ] Reconcile remaining small utility: `make-context.ts`.
- [ ] Report net line drop.

## Verification Matrix

- Current notes backend slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run examples/notes-angee/manage.py test example.notes.tests` (38 tests).
  - [x] `uv run python -m pytest tests/test_aggregates.py tests/test_crud.py tests/test_delete_preview_tree.py`.
  - [x] `uv run python -m mypy angee/graphql addons/angee/mcp`.
  - [ ] `uv run python -m mypy angee addons` remains blocked by the pre-existing
        `angee/addons.py:63` typing-special-form issue, outside the notes Hasura slice.

- Current IAM backend/frontend document slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_iam_graphql.py tests/test_iam_permission_hub_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (77 tests).
  - [x] `uv run ruff check addons/angee/iam/schema.py addons/angee/iam/permissions.py tests/test_iam_graphql.py tests/test_iam_permission_hub_graphql.py`.
  - [x] `uv run python -m mypy addons/angee/iam angee/graphql`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/iam test -- --runInBand`.
  - [x] `pnpm --filter @angee/iam typecheck`.

- Current Storage backend/frontend document slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_storage.py tests/test_aggregates.py tests/test_sdl.py -q` (59 tests).
  - [x] `uv run ruff check addons/angee/storage/schema.py angee/graphql/data/hasura.py angee/graphql/data/__init__.py tests/test_storage.py tests/test_aggregates.py examples/notes-angee/addons/example/notes/tests/test_schema_metadata.py`.
  - [x] `uv run python -m mypy addons/angee/storage angee/graphql/data`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/storage test -- --runInBand`.
  - [x] `pnpm --filter @angee/storage typecheck`.
  - [x] Adapter owner gate: `uv run ruff check strawberry_django_hasura tests`,
        `uv run python -m pytest -q` (75 tests), and
        `uv run python -m mypy strawberry_django_hasura` in the sibling
        `/Users/alexis/Work/angee/strawberry-django-hasura` checkout.

- Current Knowledge backend/frontend document slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema`.
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_knowledge_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (49 tests).
  - [x] `uv run ruff check addons/angee/knowledge/schema.py tests/test_knowledge_graphql.py`.
  - [x] `uv run python -m mypy addons/angee/knowledge angee/graphql/data`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/knowledge test -- --runInBand`.
  - [x] `pnpm --filter @angee/knowledge typecheck`.

- Current Integrate connection-substrate slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema`.
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_integrate_graphql.py tests/test_iam_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (90 tests).
  - [x] `uv run ruff check angee/graphql/data/hasura.py angee/graphql/data/__init__.py addons/angee/integrate/schema.py addons/angee/iam_integrate_oidc/schema.py addons/angee/iam_integrate_oidc/models.py tests/test_integrate_graphql.py tests/test_iam_graphql.py`.
  - [x] `uv run python -m mypy angee/graphql/data addons/angee/integrate addons/angee/iam_integrate_oidc`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/integrate test -- --runInBand`.
  - [x] `pnpm --filter @angee/integrate typecheck`.
  - [x] `pnpm --filter @angee/iam test -- --runInBand`.
  - [x] `pnpm --filter @angee/iam typecheck`.

- Current Integrate full-resource slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema`.
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_integrate_graphql.py tests/test_iam_graphql.py tests/test_agents_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (118 tests).
  - [x] `uv run ruff check addons/angee/integrate/schema.py tests/test_integrate_graphql.py tests/test_agents_graphql.py addons/angee/iam_integrate_oidc/schema.py addons/angee/iam_integrate_oidc/models.py angee/graphql/data/hasura.py angee/graphql/data/__init__.py`.
  - [x] `uv run python -m mypy addons/angee/integrate addons/angee/iam_integrate_oidc angee/graphql/data`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/integrate test -- --runInBand`.
  - [x] `pnpm --filter @angee/integrate typecheck`.
  - [x] `pnpm --filter @angee/agents test -- --runInBand`.
  - [x] `pnpm --filter @angee/agents typecheck`.

- Current Agents resource slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema`.
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_agents_graphql.py tests/test_integrate_graphql.py tests/test_iam_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (119 tests).
  - [x] `uv run ruff check addons/angee/agents/schema.py tests/test_agents_graphql.py addons/angee/integrate/schema.py addons/angee/iam_integrate_oidc/schema.py addons/angee/iam_integrate_oidc/models.py angee/graphql/data/hasura.py angee/graphql/data/__init__.py`.
  - [x] `uv run python -m mypy addons/angee/agents addons/angee/integrate addons/angee/iam_integrate_oidc angee/graphql/data`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/agents test -- --runInBand`.
  - [x] `pnpm --filter @angee/agents typecheck`.
  - [x] Adapter owner gate in sibling `/Users/alexis/Work/angee/strawberry-django-hasura`: `uv run python -m pytest -q` (76 tests), `uv run ruff check strawberry_django_hasura tests`, and `uv run python -m mypy strawberry_django_hasura`.

- Current Parties resource slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema`.
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_parties_graphql.py tests/test_agents_graphql.py tests/test_integrate_graphql.py tests/test_iam_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (122 tests).
  - [x] `uv run ruff check addons/angee/parties/schema.py tests/test_parties_graphql.py angee/graphql/data/hasura.py`.
  - [x] `uv run python -m mypy addons/angee/parties angee/graphql/data`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/parties typecheck`.
  - [x] Adapter owner gate in sibling `/Users/alexis/Work/angee/strawberry-django-hasura`: `uv run python -m pytest -q` (77 tests), `uv run ruff check strawberry_django_hasura tests`, and `uv run python -m mypy strawberry_django_hasura`.

- Current Messaging resource slice (2026-06-23):
  - [x] `uv run examples/notes-angee/manage.py schema`.
  - [x] `uv run examples/notes-angee/manage.py schema --check`.
  - [x] `uv run examples/notes-angee/manage.py makemigrations --check`.
  - [x] `uv run python -m pytest tests/test_messaging_graphql.py tests/test_messaging.py tests/test_parties_graphql.py tests/test_integrate_graphql.py tests/test_iam_graphql.py tests/test_aggregates.py tests/test_sdl.py -q` (104 tests).
  - [x] `uv run ruff check addons/angee/messaging/schema.py tests/test_messaging_graphql.py`.
  - [x] `uv run python -m mypy addons/angee/messaging angee/graphql/data`.
  - [x] `pnpm --filter @angee-example/notes-host codegen`.
  - [x] `pnpm --filter @angee/messaging typecheck`.

- [ ] `uv run examples/notes-angee/manage.py schema --check`.
- [ ] `uv run examples/notes-angee/manage.py makemigrations --check`.
- [ ] `uv run python -m pytest -k "schema or graphql or rebac or notes or compose"`.
- [ ] `uv run python -m mypy angee addons`.
- [ ] `pnpm run typecheck`.
- [ ] `pnpm run test`.
- [ ] `pnpm run build`.
- [ ] `pnpm --filter <host> run test`.
- [ ] `pnpm --filter @angee/e2e run test`.
- [ ] Per-addon e2e after each addon migrates.
- [ ] Final repo-wide backend/frontend green gate.

## Open Decisions

- [ ] `urql` transport-only vs `graphql-request` replacement.
- [ ] `valibot` vs `zod`.
- [ ] Confirm final addon migration order after notes, IAM, Storage, Knowledge,
      and Integrate.

## Parking Lot

- [ ] Per-bucket `StrawberryConfig` / name-converter seam on `AngeeSchema`.
- [ ] Exact SQL-like `_ilike` wildcard semantics for real-Hasura portability.
- [ ] Per-field `Count(field) -> count_<field>` if nullable count semantics become
  user-facing.
