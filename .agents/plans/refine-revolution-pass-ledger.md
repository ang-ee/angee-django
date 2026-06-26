# Refine Revolution Pass Ledger

Date: 2026-06-24

Goal: rebuild the Angee frontend as a refine-native application. This is not
"leverage refine in the frontend"; the frontend is built on refine. Refine,
Hasura, TanStack Router, `@refinedev/react-table`, and
`@refinedev/react-hook-form` own generic app, resource, route, navigation, menu,
breadcrumb, table, form, cache, mutation, and live mechanics. Angee keeps only
metadata projection, addon composition, rendered presentation, domain widgets,
and authored custom operations where refine/Hasura do not own the capability.
Any Angee abstraction that competes with a refine-owned frontend concern is
deleted or rewritten as a thin projection into refine. No compatibility fallback.

## Working Rule

- Do broad rewrites first; defer broad test repair until the target shape is
  coherent.
- Hard-remove old frontend contracts instead of preserving compatibility shims.
- Prefer deletion over wrappers: when refine owns a behavior, the old Angee
  behavior disappears.
- Keep this ledger current instead of using private memory.
- When a legacy helper survives, mark why it still has an owner or what deletion
  remains.

## Owner Map

- Refine core: application resource registry, data hooks, navigation,
  breadcrumbs, menus, custom operations, auth/i18n/live provider contracts,
  cache invalidation, resource/action/id inference from router parse state.
- `@refinedev/hasura`: CRUD/list/filter/sort/page dialect for Hasura roots.
- `@refinedev/react-table` + TanStack Table: server table state bridge,
  columns, row models, selection, pagination/sort/filter handoff.
- `@refinedev/react-hook-form` + react-hook-form: form state, validation,
  submission lifecycle.
- TanStack Router + refine router provider: concrete route matching and URL
  state. The router provider must parse the active match into refine
  `{ resource, action, id, pathname, params }`, so refine can own navigation and
  breadcrumbs.
- Angee metadata: model/resource declarations, field widgets, relation axes,
  group dimensions, addon routes/menus before projection into refine.
- `@angee/data`: thin projections from Angee metadata into refine resources,
  providers, auth/i18n/live bindings, router parsing, and authored custom
  GraphQL operations not covered by stock Hasura. It must not be a second SDK of
  generic list/record/mutation hooks.
- `@angee/base`: rendered controls, domain widgets, and addon/page declaration
  syntax only; no generic CRUD, cache, table, form, breadcrumb, menu, navigation,
  or route-state ownership.
- Refine concepts replace Angee frontend concepts: `resource`, `action`, `id`,
  `useTable`, `useForm`, `useList`, `useOne`, `useCreate`, `useUpdate`,
  `useDelete`, `useNavigation`, `useBreadcrumb`, and `useMenu` are the app
  vocabulary. The old page, list-state, layout, route-authored breadcrumb, and
  generic `@angee/data` CRUD owners are removed outright.

## Completed In Current Pass

- [x] Checkpoint commit made before this pass:
  `2b86bdf2 Remove legacy data query compatibility`.
- [x] Hasura grouped bucket ordering added at the owner boundary:
  `@angee/data` owns typed `_groups(order_by:)` documents/variables and
  `@angee/base` declares default bucket key/label ordering.
- [x] Storybook preview moved from stale SDK auth provider import to
  `@angee/data` auth owner.
- [x] Route chrome derivation now collapses route-less menu groups that duplicate
  their leaf crumb, so IAM-style menu grouping no longer repeats breadcrumbs.
- [x] Stale Knowledge test mocks repaired after provider/base exports moved.
- [x] Repo-wide frontend gate passed after the above:
  `pnpm run typecheck`, `pnpm run test`, `pnpm run build`.
- [x] Model-backed resource-view surface rebound to `@refinedev/react-table`.
  The server/resource list now gets rows, totals, pagination state, sorting
  state, and the TanStack table model from refine's table hook. Angee still
  renders the toolbar/view modes and serializes URL state into the controlled
  table state. Local in-memory rows remain on direct TanStack Table because they
  are not refine resources.
- [x] Revolution target clarified: this pass rebuilds the frontend as a
  refine-native application. Hook-by-hook adoption is insufficient unless it
  deletes the parallel Angee ownership.
- [x] Route-authored breadcrumb contracts hard-removed from source:
  `BaseAddonRoute.crumb`, `BaseAddonRoute.breadcrumbs`, route-static breadcrumb
  derivation, controlled `ConsoleLayout` breadcrumbs, and addon/example crumb
  components are deleted. The layout breadcrumb now renders refine
  `useBreadcrumb()` only. Old tests still mention the removed contract and will
  be rebuilt in the final test pass.
- [x] Target corrected again: the goal is to lift refine concepts wholesale, not
  retain Angee's old page/state ownership while swapping hook
  internals.
- [x] Public page ownership renamed to resource actions:
  `ResourceList`, `ResourceCreate`, `ResourceEdit`, and `ResourceShow`.
- [x] Remaining rendered list/search/group helpers renamed to resource-view
  helpers, and the former layout module moved under `layouts`.
- [x] Generic `@angee/data` list/record/mutation hooks removed from public
  exports and callers rebound to refine hooks/custom operations.
- [x] `@angee/data` generic module names removed: the old `list.ts` and
  `record.ts` contracts are split into thin refine/Hasura glue owners
  (`filter-codec.ts`, `revisions.ts`, resource/provider helpers, and custom
  Hasura operations).
- [x] Grouped leaf record navigation now fetches rows through refine `useList`
  with the Hasura bucket filter metadata, so grouped record pagers scope to the
  opened leaf query instead of the whole resource.
- [x] Focused target-shape tests rebuilt around refine hooks/providers for
  resource actions, related rows, bulk delete, form reads/writes, storage
  actions, knowledge page actions/editor, and agent provisioning.
- [x] `FormView` is rebound to `@refinedev/react-hook-form` and
  `react-hook-form`. Refine owns record read/write/submit lifecycle through
  `useForm().refineCore`, RHF owns field state/watch/dirty/submission state,
  and `@tanstack/react-form` is removed from the base package.
- [x] SDK single-id action execution is deleted. Project codegen now owns the
  generated action documents, and `@angee/data` owns `actionRequest`,
  `extractActionOutcome`, `runActionResult`, and `useActionMutation` via refine
  `useCustomMutation`; `@angee/base` composes that into
  `useRecordActionMutation`, and addon direct callers import the data
  hook/result normalizer instead of SDK.
- [x] SDK authored query/rows/mutation ownership is deleted. `@angee/data` now
  owns `useAuthoredQuery`, `useAuthoredRows`, and `useAuthoredMutation` as
  refine `useCustom`/`useCustomMutation` glue over Hasura
  `gqlQuery`/`gqlMutation` metadata, defaulting to the active layout schema.
  Addon/base callers import authored operation hooks and typed-document helpers
  from `@angee/data`; SDK keeps only the direct document-subscription/operator
  transport quarantine because refine live subscriptions are resource-event
  oriented, not arbitrary GraphQL subscription payloads.
- [x] Rendered chrome menu ownership moved to refine. Addon menu declarations
  still compose through manifests, but `createApp` projects them into visible
  refine menu resources, schema/data resources are hidden from `useMenu` by
  default, and chrome/AppRail/AppChooser/TopMenu/SubNav/CommandPalette consume
  a base adapter over refine `useMenu()`. SDK runtime no longer exposes
  `useMenus`, and Storybook preview navigation is modeled as refine resources.
- [x] IAM, messaging, and parties collection routes now claim `resource`
  instead of leaking a stray `model` property, so relation-follow route indexing
  uses the refine/resource vocabulary consistently.
- [x] Active source/docs grep is clean for removed public contracts:
  `DataPage`, `DataView`, `ListViewState`, `ShellConfig`,
  `ShellChromeProps`, `ConsoleShell`, `PublicShell`, `useResourceList`,
  `useResourceRecord`, and `useResourceMutation`. The only non-archive hit is an
  unrelated backend comment about container runtime shells.
- [x] Authored refine custom query/mutation hooks unwrap stock
  `@refinedev/hasura` envelopes before handing data to addon callers.
- [x] Agent chatter view no longer requires the admin permission class to resolve
  the actor's own running agent session.
- [x] Chrome rail/menu projection now marks authored app roots before rendering
  refine `useMenu()` output, so route group/crumb resources do not become
  duplicate rail apps.
- [x] Notes exposes `tags` as a backend-owned JSON group dimension in
  `angee.resources`; generated SDL emits `NoteTypeGroupKey.tags: JSON`, not
  `String`.
- [x] Direct Django `JSONField` group keys are fixed at the aggregate owner in
  `strawberry-django-aggregates>=0.8.0`; Angee now resolves the published PyPI
  package, not the local sibling source.
- [x] JSON bucket drill-down is exact bucket equality, not containment. The
  rendered group adapter emits an explicit `exact` JSON filter so arrays and
  objects compile to Hasura `_eq`; `jsonContains` remains only the authored
  containment operator.
- [x] Live browser smoke is clean for Notes rail apps, date day drill-down, and
  JSON `tags` drill-down at `http://localhost:5173/notes`.
- [x] Live browser smoke is clean for Notes create, edit, delete, and delete
  preview. The disposable note was created through `/notes/new`, edited after
  dirty-state save, and deleted through the record `Actions -> Delete` cascade
  preview with no fresh browser errors and no fresh Django tracebacks.
- [x] Record-action browser pass outside Notes is clean for the seeded Storage,
  Knowledge, Integrations, and Agents surfaces. Storage folder create/rename/
  delete-preview and file trash/restore are green; Knowledge create/edit/body
  autosave/delete is green; Integrations action menus render for sources,
  template-source drawer actions, VCS bridges, OAuth providers, credentials,
  and external accounts; Agents menus/tabs render for agent provisioning records
  and inference providers. Webhooks and agent skill sources had no seeded
  records to open. The local VCS bridge `Sync now` action was invoked once and
  returned cleanly; sensitive/external actions (`Provision`, OAuth connect,
  reveal secret, revoke, refresh models, test webhook) were rendered but not
  invoked.
- [x] Storage File/Folder delete-preview metadata is now exposed by the storage
  schema owner through `delete_file` / `delete_folder`, so refine delete actions
  no longer depend on missing local assumptions.
- [x] Storage trash restore links are no longer made inaccessible by a disabled
  upload drop target; disabled file-drop state is now local drop metadata, not
  inherited `aria-disabled` on all descendants.
- [x] Knowledge page creation now asks refine create for `id` as well as `title`,
  so returned-id navigation opens the created page after the mutation resolves.
- [x] `NewPageControl` trigger ownership matches the shared inline text action:
  selecting note/folder now also opens the form popover.
- [x] Parallel agent pass completed for the record-action cleanup lane:
  Integrate, Agents, and shared SDK/data surfaces were audited separately, then
  merged back through the main worktree with focused gates.
- [x] Integrate record actions no longer duplicate local set-operation buttons
  for provider/webhook enable-disable or credential/account revoke surfaces.
  Those rows now rely on the shared refine resource/action surface.
- [x] Integrate OAuth connect has one canonical callback path:
  `/integrate/oauth/callback`. The temporary `/callback` fallback, per-record
  callback path inference, and `ConnectOAuthButton.callbackPath` prop are
  deleted.
- [x] `@angee/data` action mutations now accept explicit sibling
  `invalidateModels` and translate model labels through resource metadata into
  refine `useInvalidate()` calls. Storage/Knowledge/Integrate/Agents action
  callers can declare cache effects instead of importing SDK invalidation.
- [x] Agents provision/deprovision/refresh-model/refresh-source actions now
  declare refine invalidation targets through `@angee/data`/`@angee/base`
  action hooks. Agents no longer imports `useModelInvalidation`.
- [x] SDK `document-query.ts`, `document-mutation.ts`, and the public
  one-model `useModelInvalidation` hook are deleted. Remaining SDK live
  invalidation is narrowed to the authored subscription/refetch quarantine.
- [x] `ResourceList` supports collection-only resources with no form
  declaration. The Agents `SkillsPage` fake read-only form is deleted; the page
  now declares only its resource list.
- [x] Integrate `AddRepositoryControl` no longer authors the VCS bridge picker
  query. Bridge options come from the shared refine-backed relation options
  owner; the dialog keeps only the host repository search/add custom operations.
- [x] Agent lifecycle action eligibility now lives on the Agent model and is
  projected as `can_provision`, `can_deprovision`, and `can_delete`. The Agents
  frontend consumes those booleans instead of decoding lifecycle/workspace/service
  policy locally.
- [x] Agent delete is enforced by the Hasura write backend through a model-owned
  delete guard, so rendered/provisioned agents cannot be deleted just because a
  client reaches the generic `delete_agents_by_pk` mutation.
- [x] SDK `stable-deps.ts`, `stable-deps.test.tsx`, and
  `disabled-documents.ts` are deleted. The remaining subscription/live quarantine
  keeps tiny local memo/sentinel helpers until that quarantine itself moves.
- [x] `@angee/data` no longer depends on `@angee/sdk`. Generated resource
  metadata contracts/context, active schema scope, transport auth helpers,
  i18n primitives, and `typeNameForModel` moved to `@angee/data`; SDK now
  depends inward on data and only bridge re-exports these owners while its
  runtime surface remains.
- [x] SDK relay invalidation is deleted. `createApp` no longer mounts
  `RelayInvalidationProvider`; `relay-invalidation.tsx`, `relay-registry.ts`,
  their tests, and the relay-only subscription document test are gone. Refine
  `liveProvider` owns resource live refresh, refine `useInvalidate` owns
  resource cache invalidation, and authored custom query invalidation is tagged
  through TanStack Query metadata in `@angee/data`.
- [x] SDK arbitrary document subscription and typed-document exports are deleted.
  The only remaining arbitrary subscription use is the operator daemon urql
  quarantine, now package-local in `@angee/operator`; operator console connection
  lookup uses `@angee/data` authored/refine transport instead of SDK
  `useSchemaClients`, and daemon client auth/ws helpers come from `@angee/data`.
- [x] SDK app urql provider/client/cache and SDL metadata parser are deleted.
  `createApp` no longer mounts `GraphQLClientProvider` or `GraphQLProvider`;
  layout routes bind active schema + `ModelMetadataProvider` from `@angee/data`.
  `@angee/data` projects `ModelMetadata` directly from backend
  `angee.resources`, the example host no longer imports raw SDL for app boot,
  and SDK no longer ships `graphql-client.ts`, `graphql-provider.tsx`,
  `cache-config.ts`, `schema-object-types.ts`, `model-metadata.tsx`, or
  `graphql`/`urql` dependencies.
- [x] Backend resource metadata now emits enum value inventories. Enum options
  and labels are artifact-owned, so deleting SDL metadata parsing does not drop
  enum choice labels.
- [x] Public `GroupListView` is deleted. `ListView` is now the single
  grouped-capable resource list surface, `List` defaults to it, addons no
  longer pass `list={GroupListView}`, and source grep is clean for the removed
  public name.
- [x] Docs/source ownership wording is aligned with the current target:
  active docs no longer tell callers to use SDK `DocumentVariables`, SDK/urql
  transport ownership, route/static breadcrumb ownership, `DataPage`, or SDL
  metadata. Source comments now say resource metadata. `@angee/iam` no longer
  declares the stale `urql` dependency; urql remains only in the operator daemon
  quarantine.
- [x] Refine `accessControlProvider` is wired from backend resource capability
  metadata. `@angee/data` owns `createAngeeAccessControlProvider`, maps standard
  Refine actions (`show`→`detail`, `edit`→`update`, `deleteMany`→`delete`),
  allows menu-only resources, and denies backend resources whose artifact does
  not expose the matching capability. `createApp` mounts the provider, and
  `useBulkDelete` also honors Refine `useCan` before offering the delete-preview
  flow.
- [x] Refine i18n provider is backed by `i18next` instead of Angee's custom
  message lookup/interpolation loop. `@angee/data` owns the dependency and
  provider; SDK no longer declares `i18next`.
- [x] Refine `notificationProvider` is wired over the Base UI toast renderer.
  `createApp` mounts the provider inside the runtime/toast owner, keyed refine
  notifications map to toast ids, progress notifications remain open, and
  undo/cancel actions render through the shared feedback surface.
- [x] Protected layout auth gating moved to TanStack Router `beforeLoad`.
  `createApp` reuses the same Refine auth provider for route checks and for
  `<Refine>`, redirects anonymous matches to `/login?next=...` before protected
  pages render, and deletes the old `RequireAuth` effect bounce/status copy.
- [x] `validateRoutes` is deleted. The route builder still fails fast when an
  addon declaration references no known parent or layout, but parent route
  objects now own nesting through TanStack Router `getParentRoute`; Angee no
  longer revalidates same-layout or path-prefix string rules.
- [x] Route-static chrome is deleted. `route-static-data.ts`, `useRouteChrome`,
  static `chrome` route data, layout `title`/`icon` props, and route chrome
  ambiguity checks are gone. Chrome tests now assert Refine breadcrumb/menu
  output, and labels/icons remain only as Refine resource metadata/menu facts.
- [x] The exposed Notes route-resource gap is closed. The Notes collection route
  now claims `notes.Note`, so generated `angee.resources` projects `/notes`,
  `/notes/:id`, and `/notes/new` into the Refine resource; `/notes/$id`
  breadcrumbs render through Refine as `Notes > Show`. The base i18n bundle now
  includes Refine standard action/button labels.
- [x] `statusLabel` moved out of list internals into the base `lib` owner, and
  the views barrel no longer exports `enumValueLabel`/`statusLabel` from
  `ListInternals`. Operator state tags still consume the public helper through
  `@angee/base`, but the list-internal enum fallback stays private.
- [x] Agents unique resource routes now claim their Refine resources:
  `agents.Agent`, `agents.Skill`, `agents.MCPServer`, `agents.MCPTool`,
  `agents.InferenceProvider`, and `agents.InferenceModel`. Filtered alternate
  views (`agents.templates`, `agents.sources`) intentionally do not claim
  duplicate resources already owned elsewhere.
- [x] Single-id action mutations no longer use a runtime string-built GraphQL
  document in `@angee/data`. The project action codegen now emits per-schema
  `actionDocuments` AST registries from the SDL action allow-list, `createApp`
  accepts generated `operationDocuments` per schema, and `useActionMutation`
  resolves the active schema's generated document before calling Refine
  `useCustomMutation`.
- [x] Delete-preview mutations no longer use a runtime string-built GraphQL
  document in `@angee/data`. Project operation codegen emits per-schema
  `deletePreviewDocuments` registries from the backend `angee.resources`
  artifact, `createApp` passes them through `operationDocuments`, and
  base/storage/knowledge delete flows resolve the generated document before
  calling Refine `useCustomMutation`.
- [x] Revision queries no longer use a runtime string-built GraphQL document in
  `@angee/data`. Project operation codegen emits per-schema `revisionDocuments`
  registries from the backend `angee.resources` artifact, `createApp` passes
  them through `operationDocuments`, and `useResourceRevisions` resolves the
  generated document before calling Refine `useCustom`.
- [x] Aggregate queries no longer use a runtime string-built GraphQL document in
  `@angee/data`. Project operation codegen emits per-schema `aggregateDocuments`
  registries from the backend `angee.resources` artifact, `createApp` passes
  them through `operationDocuments`, and `useAngeeAggregate` resolves the
  generated document before calling Refine `useCustom`.
- [x] Model list fetches no longer use a runtime string-built GraphQL document in
  `@angee/data`. Base list callers now pass provider-native `meta.fields`,
  `filters`, and `sorters` into Refine `useList` / `@refinedev/react-table`; the
  stock `@refinedev/hasura` provider builds the list and aggregate-count
  documents.

## Revolution Targets

- [x] Replace old public page owners with refine resource action
  surfaces. Any replacement should be a renderer/layout over refine resource,
  action, table, form, and navigation state.
- [x] Delete/demote old list state to private resource-view render adapters while
  callers consume refine table/list state as the source of truth.
- [x] Hard-remove route `crumb` / `breadcrumbs` APIs and route-static breadcrumb
  derivation; layout breadcrumb renders refine `useBreadcrumb()` only.
- [x] Project addon menus/routes and generated model resources into one refine
  resource registry, including labels, parent relationships, list/show/create/edit
  paths, and schema/provider metadata.
- [x] Render chrome menus from refine `useMenu()` instead of the SDK runtime
  menu context.
- [x] Make the TanStack router provider parse route matches into refine
  resource/action/id state; use refine breadcrumbs/navigation from that state.
- [x] Rebuild model-backed list surfaces on `@refinedev/react-table` with refine
  table state as the server/table owner.
- [x] Replace remaining generic list pagination/sort/filter plumbing in
  `@angee/base` with refine table state projections, and remove the duplicated
  `@angee/data` list hook.
- [x] Rebind `FormView` to `@refinedev/react-hook-form`; refine form core owns
  record reads/writes/submit lifecycle and RHF owns field state.
- [x] Rebind record/detail mutation and refresh flows to refine form, mutation,
  and invalidation primitives rather than SDK/request-local refresh paths.
- [x] Delete SDK single-id action hooks after callers move to
  `@angee/data` + refine `useCustomMutation`.
- [x] Delete `@angee/data` runtime single-id action document construction after
  callers move to generated per-schema action documents.
- [x] Delete `@angee/data` runtime delete-preview document construction after
  callers move to generated per-schema delete-preview documents.
- [x] Delete `@angee/data` runtime revision document construction after callers
  move to generated per-schema revision documents.
- [x] Delete `@angee/data` runtime aggregate document construction after callers
  move to generated per-schema aggregate documents.
- [x] Delete `@angee/data` runtime list document construction after model list
  callers move to provider-native Refine/Hasura fields, filters, and sorters.
- [x] Move or explicitly quarantine SDK authored query/mutation/subscription
  hooks after bespoke callers move to codegen documents plus refine `useCustom*`.
- [x] Collapse the public grouped-list synonym into `ListView`; grouping is a
  first-class `ListView` capability instead of a separate page/list owner.
- [x] Wire Refine `accessControlProvider` from backend resource capabilities.
  The existing `capabilities` artifact field is the owner, so no second frontend
  permission map was introduced.
- [x] Move Refine i18n provider to i18next-native lookup/interpolation and move
  the dependency to the data/refine owner.
- [x] Wire Refine notification provider over the shared Base UI toast renderer.
- [x] Replace rendered `RequireAuth` redirects with TanStack Router
  `beforeLoad` auth redirects.
- [x] Delete `validateRoutes` string-rule ownership; keep only route-tree
  declaration resolution that is needed to call `getParentRoute`.
- [x] Delete route-static chrome title/icon derivation; use Refine
  resource/menu metadata as the only navigation label/icon owner.
- [x] Project Notes nested record routes into the generated Refine resource so
  record breadcrumbs do not need any route-static fallback.
- [x] Remove list-internal label helpers from the public views barrel.
- [x] Add missing route-level resource claims for unique Agents resource pages.
- [x] Rebuild focused unit tests around the target shape after the broad pass.
  Full browser smoke remains a separate UX gate.

## Current Verification

- [x] Latest form-owner slice: `pnpm --filter @angee/base typecheck`
- [x] Latest form-owner slice:
  `pnpm --filter @angee/base exec vitest run src/views/FormView.test.tsx src/views/RelationPicker.test.tsx src/views/ResourceList.test.tsx src/views/ResourceList.routed.test.tsx --reporter=dot`
- [x] Latest action-owner slice: `pnpm --filter @angee/data typecheck`
- [x] Latest action-owner slice:
  `pnpm --filter @angee/data test -- operations --runInBand`
- [x] Latest action-owner slice: `pnpm --filter @angee/base typecheck`
- [x] Latest action-owner slice: `pnpm --filter @angee/integrate typecheck`
- [x] Latest action-owner slice: `pnpm --filter @angee/agents typecheck`
- [x] Latest action-owner slice:
  `pnpm --filter @angee/base exec vitest run src/views/record-action.test.tsx --reporter=dot`
- [x] Latest authored-operation slice: `pnpm --filter @angee/data typecheck`
- [x] Latest authored-operation slice: `pnpm --filter @angee/sdk typecheck`
- [x] Latest authored-operation slice: `pnpm --filter @angee/base typecheck`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/iam typecheck`,
  `pnpm --filter @angee/integrate typecheck`,
  `pnpm --filter @angee/agents typecheck`,
  `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee/storage typecheck`,
  `pnpm --filter @angee/platform typecheck`,
  `pnpm --filter @angee/resources typecheck`,
  `pnpm --filter @angee/parties typecheck`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/base exec vitest run src/views/AuthoredRowsList.test.tsx src/createApp.test.ts --reporter=dot`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/integrate exec vitest run src/views/AddRepositoryControl.test.tsx src/connect/OAuthConnectCallbackPage.test.tsx --reporter=dot`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/knowledge exec vitest run src/views/KnowledgePage.test.tsx src/data/use-page-editor.test.tsx --reporter=dot`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/storage exec vitest run src/views/StoragePage.test.tsx src/data/actions.test.tsx --reporter=dot`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/iam exec vitest run src/views/IdentityViews.test.tsx src/views/OverviewPage.test.tsx src/OAuthCallbackPage.test.tsx --reporter=dot`
- [x] Latest authored-operation slice:
  `pnpm --filter @angee/platform exec vitest run src/lib/explorer.test.tsx --reporter=dot`
- [x] Latest authored-operation slice: `pnpm run typecheck`
- [x] Latest menu-owner slice: `pnpm --filter @angee/sdk typecheck`
- [x] Latest menu-owner slice: `pnpm --filter @angee/data typecheck`
- [x] Latest menu-owner slice: `pnpm --filter @angee/base typecheck`
- [x] Latest menu-owner slice:
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/layouts/ConsoleLayout.smoke.test.tsx src/chrome/Breadcrumb.test.tsx --reporter=dot`
- [x] Latest menu-owner slice:
  `pnpm --filter @angee/sdk exec vitest run src/runtime.test.tsx src/define-addon.test.ts --reporter=dot`
- [x] Latest menu-owner slice:
  `pnpm --filter @angee/data exec vitest run src/resources.test.ts src/router.test.tsx --reporter=dot`
- [x] Latest route-resource cleanup:
  `pnpm --filter @angee/iam typecheck`,
  `pnpm --filter @angee/messaging typecheck`,
  `pnpm --filter @angee/parties typecheck`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts --reporter=dot`
- [x] Latest menu-owner slice: `pnpm --filter @angee/storybook typecheck`
- [x] Latest menu-owner slice: `pnpm run typecheck`
- [x] `pnpm run typecheck`
- [x] `pnpm --filter @angee/data test -- filter-codec operations resources router --runInBand`
- [x] `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx src/views/ResourceList.routed.test.tsx src/views/FormView.test.tsx src/views/RelatedRowsList.test.tsx src/views/DeleteBulkFlow.test.tsx src/views/RelationPicker.test.tsx src/views/resource-view-model.test.ts src/views/group-dimension.test.ts src/chrome/Breadcrumb.test.tsx src/createApp.test.ts --reporter=dot`
- [x] `pnpm --filter @angee/agents exec vitest run src/views/AgentProvisioning.test.tsx --reporter=dot`
- [x] `pnpm --filter @angee/knowledge exec vitest run src/views/KnowledgePage.test.tsx src/data/use-page-editor.test.tsx src/data/use-page-actions.test.tsx --reporter=dot`
- [x] `pnpm --filter @angee/storage exec vitest run src/data/actions.test.tsx --reporter=dot`
- [x] `git diff --check`
- [x] Latest authored hook envelope slice:
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts --reporter=dot`
- [x] Latest agent chatter slice:
  `uv run pytest tests/test_agents_graphql.py -k resolve_session_for_view -q`
- [x] Latest menu/root projection and grouping slice:
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/chrome/refine-menu.test.ts src/chrome/menu-tree.test.ts src/views/group-dimension.test.ts src/views/ResourceList.test.tsx --reporter=dot`
- [x] Latest data operation slice:
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts src/filter-codec.test.ts src/operations.test.ts src/resources.test.ts --reporter=dot`
- [x] Latest Notes metadata slice:
  `uv run examples/notes-angee/manage.py test example.notes.tests.test_schema_metadata -v 2`
- [x] Latest sibling aggregate JSON key slice:
  `cd ../strawberry-django-aggregates && uv run pytest tests/test_jsonb_groupby.py -q`
- [x] Latest aggregate dependency slice:
  `uv lock --refresh-package strawberry-django-aggregates` resolved
  `strawberry-django-aggregates==0.8.0` from PyPI, and
  `uv run examples/notes-angee/manage.py schema --check` passed.
- [x] Latest record-action smoke slice:
  live browser pass over `/storage`, `/knowledge`, `/integrate`,
  `/integrate/repositories`, `/integrate/vcs`, `/integrate/webhooks`,
  `/integrate/sources`, `/integrate/templates`, `/integrate/providers`,
  `/integrate/credentials`, `/integrate/accounts`, `/agents`,
  `/agents/providers`, and `/agents/sources`; browser console and Django log
  tail stayed clean.
- [x] Latest record-action test slice:
  `uv run pytest tests/test_storage.py::test_storage_resource_metadata_exposes_delete_previews -q`,
  `uv run ruff check addons/angee/storage/schema.py tests/test_storage.py`,
  `pnpm --filter @angee/base exec vitest run src/ui/upload-drop-target.test.tsx --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/record-action.test.tsx src/views/ResourceList.test.tsx --reporter=dot`,
  `pnpm --filter @angee/storage exec vitest run src/data/actions.test.tsx src/views/StoragePage.test.tsx --reporter=dot`,
  `pnpm --filter @angee/knowledge exec vitest run src/data/use-page-actions.test.tsx src/views/KnowledgePage.test.tsx src/data/use-page-editor.test.tsx --reporter=dot`,
  `pnpm --filter @angee/integrate exec vitest run src/views/AddRepositoryControl.test.tsx src/connect/OAuthConnectCallbackPage.test.tsx --reporter=dot`,
  `pnpm --filter @angee/agents exec vitest run src/views/AgentProvisioning.test.tsx --reporter=dot`,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest schema/codegen/typecheck slice:
  `uv run examples/notes-angee/manage.py schema --check`,
  `pnpm run typecheck`, and `git diff --check`
- [x] Latest production build slice:
  `pnpm run build` (green; existing Vite/Storybook chunk-size warnings only)
- [x] Latest parallel record-action cleanup slice:
  `pnpm --filter @angee/agents typecheck`,
  `pnpm --filter @angee/integrate typecheck`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/record-action.test.tsx --reporter=dot`,
  `pnpm --filter @angee/sdk exec vitest run src/relay-invalidation.test.tsx src/document-subscription.test.tsx --reporter=dot`,
  `pnpm --filter @angee/integrate test -- src/connect/redirects.test.ts src/connect/OAuthConnectCallbackPage.test.tsx src/index.test.ts`,
  `pnpm --filter @angee/agents exec vitest run src/views/AgentProvisioning.test.tsx --reporter=dot`,
  `pnpm --filter @angee/sdk typecheck`, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest list-only/AddRepository cleanup slice:
  `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx --reporter=dot`,
  `pnpm --filter @angee/integrate exec vitest run src/views/AddRepositoryControl.test.tsx --reporter=dot`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/integrate typecheck`,
  `pnpm --filter @angee/agents typecheck`, and `git diff --check`.
- [x] Latest Agents lifecycle/delete owner cleanup slice:
  `uv run pytest tests/test_agents_graphql.py::test_agent_hasura_insert_update_and_delete tests/test_agents_graphql.py::test_agent_hasura_delete_blocks_rendered_agents -q`,
  `uv run ruff check addons/angee/agents/models.py addons/angee/agents/schema.py angee/graphql/data/hasura.py tests/test_agents_graphql.py`,
  `uv run examples/notes-angee/manage.py schema --check`,
  `pnpm --filter @angee/agents typecheck`,
  `pnpm --filter @angee/agents exec vitest run src/views/AgentProvisioning.test.tsx --reporter=dot`,
  `uv run pytest tests/test_agents_graphql.py -q`, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest SDK stable/disabled deletion slice:
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk exec vitest run src/document-subscription.test.tsx src/relay-invalidation.test.tsx --reporter=dot`,
  and `pnpm --filter @angee/data typecheck`.
- [x] Latest SDK authored-subscription wrapper deletion slice:
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk exec vitest run src/document-subscription.test.tsx src/relay-invalidation.test.tsx --reporter=dot`,
  and `pnpm --filter @angee/operator typecheck`.
- [x] Latest data/sdk dependency inversion + relay deletion slice:
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts src/operations.test.ts src/resources.test.ts src/provider.test.ts --reporter=dot`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/sdk exec vitest run src/model-metadata.test.tsx src/graphql-client.test.ts src/i18n.test.ts src/document-subscription.test.tsx --reporter=dot`,
  `pnpm --filter @angee/sdk exec vitest run src/selection.test.ts --reporter=dot`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts --reporter=dot`,
  `pnpm run typecheck`,
  and `git diff --check`.
- [x] Latest operator subscription quarantine move:
  `pnpm --filter @angee/operator typecheck`,
  `pnpm --filter @angee/operator exec vitest run src/data/document-subscription.test.tsx src/index.test.ts src/views/sections/DetailSurfaceSections.test.tsx --reporter=dot`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/data typecheck`, and `git diff --check`.
- [x] Latest app urql/SDL parser deletion + greenfield-plan fold:
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm run typecheck`,
  `uv run examples/notes-angee/manage.py schema`,
  `uv run examples/notes-angee/manage.py schema --check`,
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts src/operations.test.ts src/resources.test.ts src/provider.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/views/ResourceList.test.tsx src/views/FormView.test.tsx --reporter=dot`,
  `pnpm --filter @angee/sdk exec vitest run --reporter=dot`,
  grep for old app SDK provider/parser symbols under app packages is clean, and
  `git diff --check`.
- [x] Latest grouped-list/docs cleanup:
  `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx src/views/ResourceList.routed.test.tsx src/views/ListView.test.tsx src/views/group-dimension.test.ts src/views/model-metadata-defaults.test.ts --reporter=dot`,
  `pnpm run typecheck`, removed source vocabulary grep is clean for
  `SDL metadata` / `DataPage` / `DataView` / `DataToolbar` / `GroupListView`,
  app urql dependency grep shows only the operator daemon quarantine, and
  `git diff --check`.
- [x] Latest access-control provider slice:
  `pnpm --filter @angee/data exec vitest run src/access-control.test.ts src/resources.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx src/views/ResourceList.routed.test.tsx src/views/DeleteBulkFlow.test.tsx src/views/record-action.test.tsx --reporter=dot`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest i18next provider slice:
  `pnpm --filter @angee/data exec vitest run src/i18n.test.ts --reporter=dot`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/sdk typecheck`,
  `pnpm --filter @angee/base typecheck`, dependency grep confirms `i18next`
  is declared by `@angee/data` and not `@angee/sdk`, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest notification provider slice:
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/feedback/Toast.test.tsx src/createApp.test.ts --reporter=dot`,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest beforeLoad auth slice:
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts --reporter=dot`,
  `pnpm --filter @angee/base typecheck`, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest route validation deletion slice:
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts --reporter=dot`,
  `pnpm --filter @angee/base typecheck`, source grep is clean for
  `validateRoutes` / `isProperPathPrefix`, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest route-static chrome deletion slice:
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/layouts/ConsoleLayout.smoke.test.tsx --reporter=dot`,
  `pnpm --filter @angee-example/notes-host exec vitest run src/addon-chrome.test.tsx --reporter=dot`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee/storybook typecheck`, source grep is clean for
  `useRouteChrome` / `RouteChromeStaticData` / route-static chrome symbols,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest Notes record breadcrumb projection slice:
  `pnpm --filter @angee-example/notes-host exec vitest run src/addon-chrome.test.tsx --reporter=dot`,
  `pnpm --filter @angee-example/notes-web typecheck`,
  `pnpm --filter @angee/base typecheck`, `pnpm run typecheck`,
  route-static symbol grep is clean, and `git diff --check`.
- [x] Latest list-internal public leak cleanup:
  `pnpm --filter @angee/base typecheck`, `pnpm --filter @angee/operator typecheck`,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest Agents route-resource projection slice:
  `pnpm --filter @angee/agents typecheck`,
  `pnpm --filter @angee-example/notes-host exec vitest run src/addon-composition.test.tsx src/addon-chrome.test.tsx --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts --reporter=dot`,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest generated action-document slice:
  `pnpm --filter @angee-example/notes-host codegen`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/record-action.test.tsx src/createApp.test.ts --reporter=dot`,
  `pnpm --filter @angee/agents exec vitest run src/views/AgentProvisioning.test.tsx --reporter=dot`,
  `pnpm --filter @angee/integrate exec vitest run src/connect/OAuthConnectCallbackPage.test.tsx src/views/AddRepositoryControl.test.tsx --reporter=dot`,
  `pnpm --filter @angee-example/notes-host exec vitest run src/addon-composition.test.tsx --reporter=dot`,
  `pnpm run typecheck`, old action-document source grep clean outside the
  build-time generator, and `git diff --check`.
- [x] Latest generated delete-preview document slice:
  `pnpm --filter @angee-example/notes-host codegen`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/storage typecheck`,
  `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/DeleteBulkFlow.test.tsx src/views/record-action.test.tsx src/createApp.test.ts --reporter=dot`,
  `pnpm --filter @angee/storage exec vitest run src/data/actions.test.tsx --reporter=dot`,
  `pnpm --filter @angee/knowledge exec vitest run src/data/use-page-actions.test.tsx --reporter=dot`,
  `pnpm run typecheck`, delete-preview source grep clean outside the build-time
  generator, two-argument `deletePreviewRequest(...)` grep clean, and
  `git diff --check`.
- [x] Latest generated revision-document slice:
  `pnpm --filter @angee-example/notes-host codegen`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee-example/notes-web typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/communication/RevisionsTab.test.tsx --reporter=dot`,
  `pnpm --filter @angee-example/notes-host exec vitest run src/addon-composition.test.tsx --reporter=dot`,
  `pnpm run typecheck`, revision source grep clean for `revisionsDocument` and
  `revisionSelection`, old two-argument `revisionsRequest(...)` grep clean, and
  `git diff --check`.
- [x] Latest generated aggregate-document slice:
  `pnpm --filter @angee-example/notes-host codegen`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee-example/notes-web typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx src/views/ListView.test.tsx src/views/ResourceList.routed.test.tsx --reporter=dot`,
  `pnpm --filter @angee-example/notes-host exec vitest run src/addon-composition.test.tsx --reporter=dot`,
  `pnpm run typecheck`, aggregate source grep clean outside the build-time
  generator, old two-argument `aggregateRequest(...)` grep clean, and
  `git diff --check`.
- [x] Latest provider-native list slice:
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/messaging typecheck`,
  `pnpm --filter @angee/parties typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts src/filter-codec.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/RelatedRowsList.test.tsx src/views/ResourceList.test.tsx src/views/ResourceList.routed.test.tsx src/views/ListView.test.tsx --reporter=dot`,
  `pnpm run typecheck`, list source grep clean for `listRequest` /
  `listDocument` / `listVariables` / list `meta.gqlVariables`, and
  `git diff --check`.
- [x] Latest generated group document slice:
  `@angee/data` now accepts generated group documents through
  `groupByRequest(..., { document })`; `useAngeeGroupBy` resolves per-schema
  resource documents via `OperationDocumentsProvider`; the example codegen emits
  `groupDocuments` from backend `angee.resources` group dimensions, including
  date extraction range keys, JSON keys, and declared aggregate measures. Ran
  `pnpm --filter @angee-example/notes-host codegen`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-web typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx src/views/ListView.test.tsx src/views/group-dimension.test.ts src/views/scalar-facet.test.tsx src/views/relation-facet.test.tsx --reporter=dot`,
  `pnpm run typecheck`, `git diff --check`, and runtime source grep clean for
  `groupByDocument`.
- [x] Latest provider-native facet slice:
  deleted `facetsRequest`, `facetsDocument`, `facetsVariables`,
  `FacetsRequestOptions`, `extractFacets`, and the last `graphql.parse` import
  from `packages/data/src/operations.ts`. `useAngeeFacets` now runs TanStack
  `useQueries` over generated group documents via Refine's selected data
  provider, and extracts each facet from the normal group root. Ran
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts src/filter-codec.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/scalar-facet.test.tsx src/views/relation-facet.test.tsx src/views/ResourceList.test.tsx src/views/ListView.test.tsx --reporter=dot`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  `pnpm --filter @angee-example/notes-web typecheck`, `pnpm run typecheck`,
  `git diff --check`, and runtime source grep clean for old facet
  request/document symbols.
- [x] Latest metadata-free dialect boundary slice:
  `packages/data/src/operations.ts` no longer imports or accepts
  `DataResourceMetadata`; request builders accept only
  `{ dataProviderName, root }`, generated documents, and variables.
  `resourceOperationTarget(...)` in `metadata.tsx` owns projecting backend
  resource metadata roots into that target, and delete-preview/action callers
  resolve the target at their edge. Also removed the fake resource fixture from
  `operations.test.ts`. Ran `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`, `pnpm --filter @angee/storage typecheck`,
  `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee/data exec vitest run src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/DeleteBulkFlow.test.tsx src/views/ResourceList.test.tsx --reporter=dot`,
  `pnpm --filter @angee/storage exec vitest run src/data/actions.test.tsx --reporter=dot`,
  `pnpm --filter @angee/knowledge exec vitest run src/data/use-page-actions.test.tsx --reporter=dot`,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest backend-owned group drill-down slice:
  Hasura/resource metadata now emits a `filter` spec on group dimensions and
  date extractions. Date granularities use backend-selected `rangeKey` siblings;
  JSON group buckets declare exact equality plus JSON value parsing; enum buckets
  declare a backend value map; relation buckets declare their public-id lookup.
  `ListInternals.bucketFilterForGroup(...)` applies only the declared filter and
  no longer guesses relation fields, lowercases enums, applies JSON equality by
  scalar, or computes date ranges locally. Ran
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/views/group-dimension.test.ts src/views/ResourceList.test.tsx --reporter=dot`,
  `uv run examples/notes-angee/manage.py schema --check`,
  `uv run examples/notes-angee/manage.py test example.notes.tests.test_schema_metadata -v 2`,
  `uv run pytest tests/test_parties_graphql.py::test_public_resource_metadata_declares_people_surface -q`,
  `uv run ruff check angee/graphql/data/metadata.py angee/graphql/data/hasura.py examples/notes-angee/addons/example/notes/tests/test_schema_metadata.py tests/test_parties_graphql.py`,
  and `git diff --check`.
- [x] Latest Refine filter serializer slice:
  custom group/facet/aggregate Hasura `where` generation now runs through
  Refine `CrudFilters`. The old duplicate symbols
  `refineFiltersFromAngeeFilter` and `hasuraWhereFromAngeeFilter` are removed;
  callers parse the current URL/filter-record shorthand with
  `crudFiltersFromFilterRecord(...)`, then serialize through the single
  `hasuraWhereFromCrudFilters(...)` dialect boundary. The duplicate
  `ResourceViewFilter -> Hasura` operator switch is deleted. Remaining debt is
  the private URL/filter-record shorthand itself, now tracked for the physical
  package split. Ran `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/data exec vitest run src/filter-codec.test.ts src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/views/ResourceList.test.tsx src/views/group-dimension.test.ts src/views/scalar-facet.test.tsx src/views/relation-facet.test.tsx --reporter=dot`,
  source grep for the old filter symbols, `pnpm run typecheck`, and
  `git diff --check`.
- [x] Latest greenfield target-plan fold:
  `.agents/plans/refine-greenfield-rebuild-plan.md` is the plan of record.
  Current-thread frontend import/live audits and the backend owner audit are
  folded into the target plan; older Refine adoption plans remain historical
  evidence only. Plan-only change; no test gate required.
- [x] Latest physical package split kickoff:
  created `@angee/refine`, `@angee/resources`, `@angee/ui`, and `@angee/app`
  package scaffolds with one-way workspace dependencies. Moved the
  Refine/Hasura field/filter/sort codec from `@angee/data` to physical
  `@angee/refine`, deleted the `@angee/data` export/test, and rewired base plus
  agents/knowledge/storage callers to import `@angee/refine` directly. Renamed
  the existing platform Resources addon package from `@angee/resources` to
  `@angee/resources-addon` so the target framework metadata bridge owns the
  `@angee/resources` package name. Ran `pnpm install --offline`,
  `pnpm --filter @angee/refine typecheck`,
  `pnpm --filter @angee/refine exec vitest run src/filter-codec.test.ts --reporter=dot`,
  `pnpm --filter @angee/data typecheck`, `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/agents typecheck`, `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee/storage typecheck`, `pnpm --filter @angee/resources typecheck`,
  `pnpm --filter @angee/resources-addon typecheck`,
  `pnpm --filter @angee-example/notes-host typecheck`,
  focused base/agents/knowledge/storage/notes-host Vitest slices, deletion greps
  for old data filter-codec symbols and duplicate `@angee/resources` owners,
  `pnpm run typecheck`, and `git diff --check`.
- [x] Latest physical `@angee/refine` operation-contract move:
  moved generated-operation request/extraction helpers, operation document
  provider/types, and typed-document helpers from `@angee/data` into physical
  `@angee/refine`; removed their `@angee/data` public exports; deleted the old
  runtime selection helper; rewired base/addon callers to import operation
  contracts from `@angee/refine` directly. Ran
  `pnpm --filter @angee/refine typecheck`,
  `pnpm --filter @angee/refine exec vitest run src/filter-codec.test.ts src/operations.test.ts --reporter=dot`,
  typechecks for `@angee/data`, `@angee/base`, `@angee/iam`,
  `@angee/integrate`, `@angee/operator`, `@angee/agents`,
  `@angee/knowledge`, `@angee/storage`, `@angee/platform`,
  `@angee/resources-addon`, `@angee/storybook`, and
  `@angee-example/notes-host`, focused base/storage/knowledge/integrate/
  notes-host Vitest slices, deletion greps for old `@angee/data` operation
  contract imports/exports, and `pnpm run typecheck`.
- [x] Latest physical `@angee/refine` provider/transport move:
  moved `provider.ts`, `provider.test.ts`, and `transport-auth.ts` from
  `@angee/data` into physical `@angee/refine`; cut the provider's metadata
  dependency down to a small `AngeeLiveResource` shape; removed provider/client/
  live/transport exports from `@angee/data`; rewired base app creation,
  Storybook fixtures/preview, `@angee/data` auth, and the operator daemon
  quarantine to import the new owner directly. Also moved the Hasura provider
  dependency to `@angee/refine` and removed stale form/table/provider deps from
  `@angee/data`. Ran `pnpm install --offline`,
  `pnpm --filter @angee/refine typecheck`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/operator typecheck`,
  `pnpm --filter @angee/storybook typecheck`,
  `pnpm --filter @angee/refine exec vitest run src/provider.test.ts src/filter-codec.test.ts src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/data exec vitest run src/auth.test.ts src/authored-hooks.test.ts src/resources.test.ts --reporter=dot`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/layouts/ConsoleLayout.smoke.test.tsx --reporter=dot`,
  `pnpm --filter @angee/operator exec vitest run src/data/document-subscription.test.tsx src/index.test.ts src/views/sections/DetailSurfaceSections.test.tsx --reporter=dot`,
  stale provider/transport import/dependency deletion greps, `pnpm run typecheck`,
  and `git diff --check`.
- [x] Latest physical `@angee/refine` router move:
  moved the TanStack Router provider bridge (`router.tsx`, `router.test.ts`)
  from `@angee/data` into physical `@angee/refine`; removed the old data export;
  rewired base app creation and Storybook preview to import the router provider
  from `@angee/refine`; moved the TanStack Router peer/dev dependency off
  `@angee/data` and onto `@angee/refine`. Ran
  `pnpm install --offline`, `pnpm --filter @angee/refine typecheck`,
  `pnpm --filter @angee/refine exec vitest run src/router.test.ts src/provider.test.ts src/filter-codec.test.ts src/operations.test.ts --reporter=dot`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/layouts/ConsoleLayout.smoke.test.tsx src/chrome/Breadcrumb.test.tsx --reporter=dot`,
  `pnpm --filter @angee/storybook typecheck`, stale router/provider import and
  dependency deletion greps, and `pnpm run typecheck`.
- [x] Latest physical `@angee/resources` metadata/projection move:
  moved generated resource artifact contracts, metadata React providers/context,
  schema-field metadata projection, Refine resource projection, resource type
  contracts, and row identity helpers from `@angee/data` into physical
  `@angee/resources`; removed the old data exports/files; rewired base, data,
  Storybook, and agents/knowledge/storage/integrate callers to import
  `@angee/resources` directly. Ran `pnpm install --offline`,
  `pnpm --filter @angee/resources typecheck`,
  `pnpm --filter @angee/resources exec vitest run src/resources.test.ts src/resource-types.test.ts src/rows.test.ts --reporter=dot`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts src/access-control.test.ts src/auth.test.ts --reporter=dot`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/agents typecheck`,
  `pnpm --filter @angee/knowledge typecheck`,
  `pnpm --filter @angee/storage typecheck`,
  `pnpm --filter @angee/integrate typecheck`,
  `pnpm --filter @angee/storybook typecheck`, focused base/knowledge/storage/
  agents Vitest slices, deletion greps for moved metadata/resource/row imports
  from `@angee/data`, and `pnpm run typecheck`. Follow-up: remove the stale
  manifest-only `@angee/resources -> @angee/refine` edge; source imports already
  show the resource package does not need the dialect package.
- [x] Latest physical `@angee/resources` capability/access-control cleanup:
  moved `createAngeeAccessControlProvider` and `capabilityForRefineAction` from
  `@angee/data` into physical `@angee/resources`; removed the old data export;
  rewired `createApp` to import the provider factory from the resource owner;
  removed the stale manifest-only `@angee/resources -> @angee/refine` edge and
  refreshed the lockfile. Ran `pnpm install --offline`,
  `pnpm --filter @angee/resources typecheck`,
  `pnpm --filter @angee/resources exec vitest run src/access-control.test.ts src/resources.test.ts src/resource-types.test.ts src/rows.test.ts --reporter=dot`,
  `pnpm --filter @angee/data typecheck`,
  `pnpm --filter @angee/base typecheck`,
  `pnpm --filter @angee/base exec vitest run src/createApp.test.ts src/views/DeleteBulkFlow.test.tsx --reporter=dot`,
  source grep confirming the access-control symbols live only in
  `@angee/resources` plus the `createApp` mount, and lockfile grep confirming
  `packages/resources` depends on `@refinedev/core` without `@angee/refine`.
- [x] Latest invalidation owner split:
  deleted `packages/data/src/invalidation.ts`. Resource-model-label to Refine
  invalidation target projection now lives in physical `@angee/resources`
  (`resourceInvalidationTargets`, `refineInvalidationParams`), while the
  authored-query TanStack Query metadata exception now lives in physical
  `@angee/refine` (`authoredQueryMeta`, `authoredQueryReadsAnyModel`). Data
  hooks import both owners instead of hosting invalidation semantics. Ran
  `pnpm --filter @angee/resources typecheck`,
  `pnpm --filter @angee/resources exec vitest run src/invalidation.test.ts src/access-control.test.ts src/resources.test.ts --reporter=dot`,
  `pnpm --filter @angee/refine typecheck`,
  `pnpm --filter @angee/data typecheck`, and
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts --reporter=dot`.
- [x] Latest stable custom-operation dependency cleanup:
  moved `useStableArray`, `useStableValue`, and `useStableVariables` from
  `@angee/data` to physical `@angee/refine`; deleted
  `packages/data/src/stable-deps.ts`; rewired `authored-hooks.tsx` to import the
  helpers from `@angee/refine`. The only remaining duplicate stable-variable
  helper is intentionally package-local to the operator daemon subscription
  quarantine. Ran `pnpm --filter @angee/refine typecheck`,
  `pnpm --filter @angee/data typecheck`, and
  `pnpm --filter @angee/data exec vitest run src/authored-hooks.test.ts --reporter=dot`.
- [x] Latest target-plan fold refresh:
  `.agents/plans/refine-greenfield-rebuild-plan.md` remains the single target
  plan of record. It now explicitly states that new research notes fold into
  that file instead of becoming parallel checklists, and records the active
  backend rider state: `hasura_config()` / `pin_snake_wire_names` removal is in
  the current worktree, while frontend authored operation documents/callers/
  codegen still need the snake_case root cutover before the slice is complete.
  Plan-only change; no runtime gate required.

## Backend Data-Layer DRY Pass (2026-06-24)

Evidence: architecture-reviewer mapped 7 reinvention/DRY findings in
`angee/graphql/data/{metadata,hasura}.py` (Angee re-deriving facts the dataclasses
or the stack libraries already own). Executing highest-win-first, behavior-preserving
slices verified by `schema --check` (the `runtime/schemas/*.metadata.json` oracle,
emitted with `sort_keys=True` so only keys/values must match).

- [x] Finding 5 — metadata dataclasses own their wire shape. Deleted the ~170-line
  hand-transcribed JSON mirror (`_serialize_data_resource` body +
  `_serialize_group_bucket_filter`) and the two hand-maintained field-name lists
  (`_RESOURCE_ROOT_FIELDS`/`_RESOURCE_TYPE_FIELDS`). One recursive `_wire_dataclass`
  serializer reads `dataclasses.fields()`; irregular wire keys (`group_name→groups`,
  `from_value→from`, …) and non-wire type handles (`model`/`*_type`) ride
  `field(metadata={"wire": ...})`; `_merge_roots`/`_merge_type_names` iterate the
  dataclass field inventory. Net −145 lines in `metadata.py`.
- [x] Finding 6 — `is_to_one_relation`/`is_to_many_relation` deduped into the shared
  `angee.graphql.introspection` owner; both private copies in `metadata.py`/`hasura.py`
  deleted.
- [x] Finding 7 — node GraphQL prefix read from `resource_type_name(node)` (the node
  is in hand) instead of `_node_type_name` suffix-scanning `resource.types` for a name
  ending in `"Aggregate"`. Deleted `_node_type_name`.

Verified: `ruff check` clean on the three files; `schema --check: ok` (metadata.json
byte-identical); `test_schema_metadata`, `tests/test_parties_graphql.py`,
`tests/test_storage.py` resource-metadata tests green. Net −154 lines across the
data layer.

- [x] Finding 4 — `hasura_resource(...)` defaults the four mechanical knobs from
  `(node, model)`: `get_queryset` → `model.objects.all()`, `get_aggregate_queryset` →
  `aggregate_queryset(read_queryset(info))` (the `scoped_for_aggregate` probe, now one
  shared public helper replacing 6 copies), `write_backend` → `AngeeHasuraWriteBackend(model)`,
  and `id_decode` → `public_pk_decoder(model)` gated on `id_column == "pk"`. Swept the
  redundant kwargs from every plain call site across 7 addon `schema.py`; custom
  backends (Folder/IAMUser/IAMGroup/Vault/Page), `public_id_fields=` write backends,
  REBAC/console `get_queryset`, differing aggregate sources (parties Handle), and the
  notes `id_column="sqid"` resource stay explicit. Net −125 in the addons.
  `schema --check: ok` (byte-identical), ruff clean, storage/parties write tests green.

Snake-case cutover — backend test gate (was missed earlier): the full backend suite
after the sweep exposed 60 pre-existing failures from the checkpoint commit —
`tests/test_{iam,iam_permission_hub,integrate,agents,knowledge,}graphql.py` still query
camelCase roots/fields (`grantRole`→`grant_role`, `updateVcsBridge`→`update_vcs_bridge`,
`missingActor`→`missing_actor`) that `hasura_config()` renamed to snake_case.
`schema --check` is byte-identical with this pass, proving these are cutover collateral,
not regressions from the DRY work. The symmetric backend-side fix (error-driven
snake_case of the test query strings + response-key reads, `$vars`/Python keys left
alone) landed across all 6 files: **`uv run pytest tests/` is now 552 passed, 0 failed**
(was 60 failing); `schema --check: ok`, `ruff check` clean, whitespace clean. The
snake-case cutover is now complete on both sides.

Two cutover observations for the architect (tests match real schema, not forced):
the revisions root is a HYBRID `markdownPage_revisions` (only the `_revisions` join was
snake-cased, model prefix stayed camelCase), and subscription fields stayed camelCase
(`pageChanged`, `userChanged`). `hasura_config()` did not reach those two surfaces —
likely a cutover gap worth a deliberate decision.

Remaining findings (deferred to the architect — not pure refactors):
- Finding 2 (read group-key/bucket/range names off `group_by_alias` instead of
  concatenating): behavior-preserving, BUT `group_by_alias` is NOT in the sibling
  `strawberry-django-aggregates` public `__all__` — it lives in `compiler.py`. Importing
  it would breach "cross boundaries only through declared contracts". The right fix is to
  publish the alias contract from the sibling lib (strengthen the owner), then import —
  a cross-repo change. Deferred.
- Findings 1 + 3 (delegate aggregate operators to `default_operators_for`; collapse the
  two scalar classifiers): these CHANGE the emitted artifact — Angee currently advertises
  a curated op subset (sum/avg/min/max) and projects Decimal→`Float` into its own
  frontend scalar vocabulary (`_RESOURCE_FIELD_SCALARS`). Delegating naively expands the
  advertised ops and emits scalars Angee's own validation rejects. The real fix is
  unifying the two Angee classifiers (DRY) and intersecting library ops with Angee's
  curation — a product-surface decision, not a pure refactor.

## Refine Rebuild — Independent Confirmation (2026-06-25)

The ledger's "rebuilt on Refine" claims were self-reported and unverified. An
independent, code-grounded architecture review (file:line evidence, not docs)
CONFIRMS the rebuild is genuine for 9 of 10 generic concerns — all wired into one
`<Refine>` root (`packages/base/src/createApp.tsx:267-280`):

| Concern | Verdict | Evidence |
|---|---|---|
| list/table + page/sort/filter | RENTED | `useTable` + TanStack Table server mode, `resource-view-surface.ts:361` |
| CRUD + cache | RENTED | `useList`/`useCustomMutation`, one react-query cache; `refine/provider.ts` |
| form | RENTED | `useForm` (RHF), `FormView.tsx:433` |
| routing | RENTED | TanStack Router + refine routerProvider, `refine/router.tsx:15` |
| menu / breadcrumb | RENTED | `useMenu`/`useBreadcrumb`, `chrome/refine-menu.ts`, `Breadcrumb.tsx` |
| auth | RENTED | authProvider + `useCan`, `createApp.tsx:268`, `useBulkDelete.ts:72` |
| notifications | RENTED | `notificationProvider`, `createApp.tsx:273` |
| live + invalidation | RENTED | `liveProvider` + `useInvalidate`; `urql` ABSENT from every app package |
| **i18n** | **MIXED** | i18next wired but bypassed by a parallel translate path (see below) |

The 53k-line `@angee/base` is NOT a reinvented framework: it is 41k non-test, and
the bulk is legitimate rendering (`ui/` styled Base UI bindings, 9.8k) + domain view
modes no library owns (Board/Gallery/Tree/Timeline/Graph). Drift scans (`role="grid"`,
second `QueryClient`, raw `<table>` outside `ui/table.tsx`, data-polling `setInterval`)
came back clean.

Two genuine, ISOLATED reinventions remain — the real residual DRY debt:

- [ ] **local-rows engine (HIGH)** — `packages/base/src/views/local-rows.ts`
  hand-rolls a Hasura `_bool_exp` operator evaluator (`matchesLocalLookup:174-217` —
  exact/inList/isNull/iContains/startsWith/gt/lt/AND/OR/NOT, the constitution's
  forbidden "inspect-to-decide" smell) plus `localRowsSort` + `.slice()` pagination,
  duplicating TanStack Table's client row models — which is ALREADY instantiated for
  these very rows (`resource-view-surface.ts:554`). Serves the no-Hasura-backend
  `RowsListView` (operator/platform/storage/messaging/parties). Fix: drive sort +
  pagination through TanStack Table client row models; keep ONE filter-predicate
  owner shared by server+client (the shared `ResourceViewFilter` codec is a real DRY
  benefit — fix the owner, don't delete it).
- [ ] **parallel i18n path (MEDIUM)** — every base component translates via
  `useNamespaceT → interpolateMessage` (`sdk/runtime.ts:96-124`,
  `data/i18n.ts:45-62`) which reads a second message store off `AppRuntime.i18n`
  and spins up a throwaway i18next instance PER CALL; the wired `i18nProvider` /
  refine `useTranslate` is used nowhere. Fix: route through one shared i18next
  instance; preserve the deliberate bundle-fallback for provider-less embeds.

Bottom line: the rebuild was genuinely done — not faked. The remaining work is these
two targeted cleanups, NOT the physical package split (which is cosmetic by
comparison).

## Open Owner Decisions

- [ ] Whether Angee URL search remains the canonical user-facing state owner or
  becomes a serialization of refine table state.
- [ ] Whether grouped/board views become refine resource meta modes or remain
  Angee-rendered domain modes over refine list/table data.
- [x] Notes nested record routes project through Refine resource/action route
  state; no route-static chrome fallback remains.
- [x] Chrome renderer consumes refine-shaped menu/breadcrumb/navigation state
  from the composed app rather than route-authored breadcrumb contracts.
