# DRY Architecture Audit Fix Plan

**Date:** 2026-06-18
**Goal:** fix the DRY, ownership, decomposition, guideline, naming, and library
ownership findings from the independent audit without turning the cleanup into a
grab bag. Each slice should leave the repo shippable and move a concern to its
proper owner.

## Ground Rules

- **Do not edit generated output as source.** Fix source owners, then regenerate
  runtime artifacts through the documented commands.
- **One owner per fact.** If a fix requires shared behavior, put it in the owner
  (`angee.compose`, `angee.graphql`, `@angee/base`, `@angee/sdk`, or the owning
  addon), then migrate callers.
- **Parallel agents need disjoint write sets.** If two slices both touch the same
  file family, sequence them.
- **Pin behavior before deleting forks.** Add focused tests around current
  behavior when replacing repeated code paths.
- **No new library unless `docs/stack.md`, manifests, and lockfiles agree.** Most
  findings should use locked owners already present.

## Dependency Leverage Targets

Before adding an Angee abstraction, each worker should ask whether the locked
stack already owns the concern. The goal is not "more dependencies"; it is less
framework code by letting the selected dependencies do their native jobs.

- [x] **Django owns model shape and relation metadata.**
  - Use `_meta`, `get_field()`, `remote_field.get_accessor_name()`,
    `Field.value_from_object()`, model methods, managers, querysets, and app
    registry lookups instead of re-decoding models or rebuilding relation names.
  - Apply this to runtime label validation, integrate reverse-accessor lookup,
    public ID helpers, and any source-model discovery heuristics.

- [x] **Strawberry / strawberry-django own GraphQL type and resolver plumbing.**
  - Keep addon schemas native Strawberry contributions; shared Angee code should
    own only merge seams, action helpers, deletion helpers, and SDL emission.
  - IAM overview facts now resolve in the backend owner; direct Django ORM
    counting fit the REBAC/peek-row shape better than a pure aggregate wrapper.

- [x] **django-zed-rebac owns permission semantics.**
  - Privileged/admin facts should come from IAM/REBAC-owned backend resolvers, not
    role-name string matching or partial frontend lists.
  - Any shared action/delete helper must preserve actor scoping, elevated lookup,
    and fail-closed behavior.

- [x] **django-sqids owns opaque external IDs.**
  - Public ID conversion should be a small base/Sqid owner API, never repeated
    through fake unsaved instances in addons.

- [x] **GraphQL Code Generator owns authored operation result/variable types.**
  - Use generated `TypedDocumentNode`s and `DocumentVariables`/inference for
    authored operations.
  - For the operator daemon, add a daemon-specific client-preset run rather than
    maintaining raw strings plus handwritten result interfaces.
  - Data-view root field metadata must be derived from the active generated SDL
    or an explicit generated metadata artifact, not guessed independently at
    every `DataPage` call site.

- [x] **urql owns GraphQL query/mutation/subscription execution.**
  - Keep local wrappers thin: they should bind Angee schemas, invalidation, and
    action-result normalization, not re-create client state machines.
  - Do not introduce TanStack Query for GraphQL data unless the stack decision
    changes.

- [x] **TanStack Table owns rows, columns, sorting, grouping, selection, and table
  keyboard behavior.**
  - Replace addon-local `<table>` implementations with `RowsListView`,
    `ListView`, or a base/operator wrapper backed by the shared table owner.

- [x] **TanStack Form owns form state.**
  - Inline create/rename controls should compose the base form/action layer or a
    small shared primitive backed by it, not repeat local state, trim validation,
    busy flags, and Escape/blur behavior.

- [x] **TanStack Router owns route matching and active route state.**
  - If new chrome/navigation cleanup appears while implementing this plan, use
    route metadata and matches rather than path-string matching in components.

- [x] **Base UI and Floating UI own headless controls and positioning.**
  - Dialogs, popovers, menus, tabs, tooltips, fields, toolbar behavior, and
    floating placement should be composed through `@angee/base` primitives that
    wrap these dependencies.

- [x] **date-fns owns date parsing/formatting.**
  - Replace per-addon `toLocaleDateString`, ISO slicing, and ad hoc `new Date()`
    parsing with a shared date/date-time formatter or widget built on date-fns.

- [x] **i18next owns user-facing copy.**
  - Base and addon component-visible strings should live in namespace bundles and
    resolve through `useBaseT()` or `use<Addon>T()`.

- [x] **Tailwind helpers own class composition.**
  - Keep one `cn()`/`tailwind-merge` path for class joining and use
    `tailwind-variants` recipes for repeated slot/variant styling.

- [x] **lucide-react is consumed through the Angee glyph registry.**
  - Add missing glyphs to the registry or addon manifest, then render through
    `<Glyph name="...">`; do not import lucide directly inside components.

- [x] **Upload drag/drop needs an owner decision.**
  - Either centralize native browser drag/drop in a base upload primitive, or lock
    `react-dropzone` in `docs/stack.md` plus manifests/lockfiles and wrap it once.

- [x] **Do not add adjacent libraries just because they are familiar.**
  - The audit found no current locked-owner case for `django-ninja`,
    `django-components`, `react-hook-form`, TanStack Query, shadcn, or Radix.
    Revisit only with a concrete stack-row change.

## Recommended Parallel Shape

Run this as a branch or Angee workspace dedicated to the cleanup. The best
parallelism is by ownership layer, not by random finding:

- **Agent A: Composer/runtime correctness**
  - Write set: `angee/compose/**`, `tests/test_compose.py`, maybe
    `docs/composer.md`.
  - Avoid: `angee/graphql/**`, frontend packages.

- **Agent B: GraphQL backend shared primitives**
  - Write set: `angee/graphql/**`, affected backend addon schema modules,
    backend tests for IAM/integrate/agents/storage/knowledge.
  - Avoid: composer/runtime and frontend packages.

- **Agent C: Typed GraphQL/codegen**
  - Write set: `examples/notes-angee/web/codegen*.ts`,
    `examples/notes-angee/web/bin/**`, `addons/angee/operator/web/**`,
    `addons/angee/agents/web/src/documents.ts`,
    `addons/angee/agents/web/src/useAcpRuntime.ts`, package scripts.
  - Avoid: general UI primitives while Agent D runs.

- **Agent D: Base frontend primitives**
  - Write set: `packages/base/src/**`, `packages/sdk/src/model-metadata*`,
    `packages/sdk/src/selection*`, base/sdk tests/stories.
  - Avoid: addon adoption except tiny compatibility shims.

- **Agent E: Addon frontend adoption**
  - Write set: `addons/angee/{agents,storage,knowledge,platform,resources,iam}/web/**`,
    maybe example addon web code.
  - Starts after Agent D lands base primitives that addons need.

- **Agent F: Docs and guideline reconciliation**
  - Write set: `docs/**`, `.agents/plans/**`.
  - Starts after code slices define the actual contracts, except for obvious
    stale prose removal that is independent.

## Wave 0 — Baseline And Scope Lock

- [x] Use the existing cleanup branch/worktree for this effort.
- [x] Record verification status:
  - [x] `uv run examples/notes-angee/manage.py angee build --check`
  - [x] `uv run examples/notes-angee/manage.py schema --check`
  - [x] `pnpm run typecheck`
  - [x] `pnpm run test`
- [x] If any baseline check is already red, capture it in `.agents/notes/` and
  keep it separate from cleanup regressions.
- [x] Decide whether this plan supersedes or should merge into existing related
  plans:
  - [x] `.agents/plans/react-consistency-cleanup.md`
  - [x] `.agents/plans/react-consistency-todo.md`
  - [x] `.agents/plans/typed-graphql-operations.md`
- [x] For each worker, do a quick dependency-owner pass against the
  **Dependency Leverage Targets** above before implementing. If a task still
  needs custom Angee code, name exactly what seam Angee owns.

## Wave 1 — Independent Correctness Fixes

These can run in parallel because their write sets are naturally separate.

### Agent A: Runtime And Composer Ownership

- [x] Fix runtime drift ownership.
  - [x] Change [runtime.py](/Users/alexis/Work/angee/angee-django/angee/compose/runtime.py:544)
    so composer drift checks ignore non-composer-owned `runtime/gql/`, or switch
    to a positive list of composer-owned files.
  - [x] Add a regression test beside
    [test_runtime_check_ignores_schema_command_output](/Users/alexis/Work/angee/angee-django/tests/test_compose.py:193).

- [x] Make schema emission converge.
  - [x] Update [sdl.py](/Users/alexis/Work/angee/angee-django/angee/graphql/sdl.py:43)
    so `emit()` prunes orphaned `*.graphql` files under the owned
    `runtime/schemas/` directory.
  - [x] Add a test proving `schema` followed by `schema --check` self-heals after
    a schema bucket is removed.
  - Note: if this touches `angee/graphql/**`, coordinate with Agent B.

- [x] Make runtime label ownership fail fast.
  - [x] Validate discovered runtime and extension source models have
    `_meta.app_label == app_config.label`.
  - [x] Add focused tests for mismatched source `Meta.app_label` and extension
    targets.

- [x] Preserve composition order for model extension bases.
  - [x] Stop re-sorting extension bases by `_meta.object_name`.
  - [x] Document or enforce a policy for non-field MRO collisions.
  - [x] Add tests proving class renames do not change extension precedence.

- [x] Resolve root/dependency duplicate semantics in `AppGraph`.
  - [x] Decide: duplicate roots fail fast or are explicitly documented/tested as
    deduped.
  - [x] Decide: `depends_on` supports labels reliably or only dotted app paths.
  - [x] Implement the chosen rule in [appgraph.py](/Users/alexis/Work/angee/angee-django/angee/compose/appgraph.py:57).

### Agent B: Backend GraphQL And Domain Owners

- [x] Move duplicated admin-delete behavior to the GraphQL owner.
  - [x] Compare `_admin_delete()` in
    [iam/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/iam/schema.py:584),
    [integrate/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/integrate/schema.py:618),
    and delete behavior in
    [crud.py](/Users/alexis/Work/angee/angee-django/angee/graphql/crud.py:211).
  - [x] Add a shared API in `angee.graphql.deletion` or extend `crud(delete=...)`
    with a `before_delete`/admin-delete hook.
  - [x] Replace addon-local copies.
  - [x] Add tests for preview, elevated lookup, blocker handling, and delete hooks.

- [x] Add shared elevated action target resolution.
  - [x] Create `angee.graphql.actions.resolve_action_target()` with actor,
    elevation, not-found, and optional `select_related` semantics.
  - [x] Replace repeated action target lookup in
    [agents/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/agents/schema.py:526)
    and
    [integrate/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/integrate/schema.py:1367).
  - [x] Add addon tests covering denied, missing, and allowed action cases.

- [x] Move IAM principal/public-id resolution out of schema-private helpers.
  - [x] Move helper behavior from
    [iam/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/iam/schema.py:527)
    to `angee.iam.identity` or `UserManager`.
  - [x] Replace private imports from
    [integrate/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/integrate/schema.py:492).
  - [x] Add public API tests at the IAM owner.

- [x] Ask Django for integrate extension reverse accessors.
  - [x] Replace formatted related-name reconstruction in
    [integrate/impl.py](/Users/alexis/Work/angee/angee-django/addons/angee/integrate/impl.py:55)
    with `remote_field.get_accessor_name()` or a classmethod on
    `IntegrationMixin`.
  - [x] Add a test that changing `related_name` in the field owner updates the
    implementation path automatically.

- [x] Add a base-owned public ID helper.
  - [x] Add `public_id_from_pk()` or `public_id_for(model, pk)` to the Sqid/base
    owner.
  - [x] Add optional GraphQL wrapper for Strawberry IDs if useful.
  - [x] Replace fake unsaved-instance patterns in storage, knowledge, and IAM.

### Agent C: Typed GraphQL Boundary

- [x] Remove the easy handwritten authored-operation variable type.
  - [x] Replace `RenderAgentPromptVariables` in
    [documents.ts](/Users/alexis/Work/angee/angee-django/addons/angee/agents/web/src/documents.ts:68)
    with `DocumentVariables<typeof RenderAgentPrompt>` or pure inference through
    `useAuthoredMutation(RenderAgentPrompt)`.
  - [x] Keep JSON scalar runtime validation on the domain payload.

- [x] Finish operator daemon typed document ownership.
  - [x] Add a daemon client-preset codegen pipeline against
    `addons/angee/operator/web/schema/operator.graphql`.
  - [x] Split the Django console `OperatorConnectionQuery` from daemon operations.
  - [x] Convert daemon operations assembled by string interpolation into static
    `graphql()` documents plus GraphQL fragments, or explicitly mark any
    impossible cases as string-runner exceptions with tests.
  - [x] Make package/root `codegen`, `typecheck`, and tests run the daemon codegen.

- [x] Reconcile with existing typed-operation plan.
  - [x] Fold reusable decisions from
    `.agents/plans/typed-graphql-operations.md` into implementation comments/docs.
  - [x] Avoid reintroducing project-neutral generated operation types into
    `@angee/sdk`; generated operation types are project/runtime output.

## Wave 2 — Shared Frontend Owners

Start these after Wave 1 is underway. Agent D can build base primitives while
Agent C works on codegen. Agent E should wait for Agent D primitives before
mass addon adoption.

### Agent D: `@angee/base` DRY Primitives

- [x] Add or expose a shared inline create/rename primitive.
  - [x] Owner: `@angee/base` form/action layer.
  - [x] Cover trim validation, Escape/blur cancellation, busy state, focus, and
    accessibility once.
  - [x] Add focused tests for keyboard and cancellation behavior.

- [x] Add operator/list adapter primitive if it belongs in base.
  - [x] If generic enough, add a compact `RowsListView`/data-view mode for embedded
    daemon status tables.
  - [x] If operator-specific, leave the generic list primitive alone and let
    Agent E add an operator runtime wrapper.

- [x] Fix the captured `VcsBridge` `DataPage` delete-root runtime error.
  - [x] Pin the repro from
    `/Users/alexis/.codex/attachments/ca885d38-88d5-447c-8aa4-be2881a422a4/pasted-text.txt`:
    `DataPage` for `integrate.VcsBridge` throws because schema metadata does not
    expose a `delete` root field. The canonical root is now `deleteVcsBridge`.
  - [x] First eliminate stale-artifact causes: run `angee build`, `schema`,
    `pnpm run codegen`, and restart Vite before judging the code path.
  - [x] Add a regression test that renders or metadata-loads `VcsBridgesPage`
    under the `console` schema and proves `integrate.VcsBridge` resolves
    `deleteVcsBridge`.
  - [x] If the active schema is wrong, fix shell/schema binding so console routes
    cannot read public-schema model metadata.
  - [x] If metadata inference is wrong, fix the metadata owner in
    `packages/sdk/src/model-metadata.tsx`; prefer generated/SDL-owned root-field
    facts over addon-local workarounds.
  - [x] Keep the current guideline contract: `DataPage` requires a delete root
    unless a real read-only `DataPage` mode is designed and implemented in base.
  - [x] Follow-up cleanup: canonicalize the source owner instead of preserving
    the workaround. `VcsBridge` now owns `vcsBridge(s)` SDL roots, VCS bridge
    action variables, `Repository.vcs_bridge`, and `integrate/vcs_bridge` REBAC
    relations; the SDK delete-root matcher no longer carries the
    old target-mismatch fallback.

- [x] Centralize status-to-tone mapping.
  - [x] Route private maps through
    [status-tones.ts](/Users/alexis/Work/angee/angee-django/packages/base/src/widgets/status-tones.ts:20).
  - [x] Add shared vocabulary entries such as `closed` or `connecting` only when
    they are cross-domain.
  - [x] Keep domain-specific overrides explicit at the column/widget boundary.

- [x] Centralize date/date-time formatting.
  - [x] Expose a date-fns-backed formatter or widget from base.
  - [x] Define invalid/empty-date behavior once.
  - [x] Add tests for ISO strings, invalid values, date-only, and date-time cases.

- [x] Move remaining base English literals into `enBaseMessages`.
  - [x] Audit
    [themePicker.tsx](/Users/alexis/Work/angee/angee-django/packages/base/src/widgets/themePicker.tsx:13),
    [LogStream.tsx](/Users/alexis/Work/angee/angee-django/packages/base/src/fragments/LogStream.tsx:35),
    [TopMenu.tsx](/Users/alexis/Work/angee/angee-django/packages/base/src/chrome/TopMenu.tsx:71),
    and
    [date.tsx](/Users/alexis/Work/angee/angee-django/packages/base/src/widgets/date.tsx:22).
  - [x] Resolve labels with `useBaseT()` in component bodies.

- [x] Decide and implement upload dropzone ownership.
  - [x] If native/base-owned: centralize drag/drop handling in a base upload
    primitive.
  - [x] If `react-dropzone` is the owner: move it from proposed to locked in
    `docs/stack.md`, update manifests/lockfiles, then wrap it in base. Not
    chosen; `docs/stack.md` now records native/base ownership.

### Agent E: Addon Frontend Adoption

- [x] Replace `AgentProvisioning` table and operator subscription duplication.
  - [x] Move daemon workspace/source subscription and result typing into
    `@angee/operator/runtime`.
  - [x] Render with `RowsListView` or the base/operator primitive from Agent D.
  - [x] Consume the runtime wrapper from agents.

- [x] Replace addon-local inline create/rename controls.
  - [x] Migrate
    [NewFolderControl.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/storage/web/src/views/NewFolderControl.tsx:23),
    [SelectedFolderControl.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/storage/web/src/views/SelectedFolderControl.tsx:28),
    and
    [NewPageControl.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/knowledge/web/src/views/NewPageControl.tsx:25).

- [x] Replace private status-tone maps.
  - [x] Migrate
    [AgentChat.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/agents/web/src/views/AgentChat.tsx:118)
    and
    [file-display.ts](/Users/alexis/Work/angee/angee-django/addons/angee/storage/web/src/lib/file-display.ts:21).

- [x] Add i18n bundles for Platform and Resources.
  - [x] Add `i18n.ts` and `usePlatformT()` / `useResourcesT()`.
  - [x] Register `i18n` in
    [platform index.ts](/Users/alexis/Work/angee/angee-django/addons/angee/platform/web/src/index.ts:53)
    and
    [resources index.ts](/Users/alexis/Work/angee/angee-django/addons/angee/resources/web/src/index.ts:30).
  - [x] Move component-visible strings out of `AddonsPage`, `ModelDetail`, and
    `ResourcesPage`.

- [x] Replace per-addon date formatting.
  - [x] Migrate
    [storage file-display.ts](/Users/alexis/Work/angee/angee-django/addons/angee/storage/web/src/lib/file-display.ts:38),
    [resources rows.ts](/Users/alexis/Work/angee/angee-django/addons/angee/resources/web/src/lib/rows.ts:25),
    and
    [knowledge PageEditor.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/knowledge/web/src/views/PageEditor.tsx:146)
    to the base date owner.

- [x] Remove local public ID aliases.
  - [x] Delete storage/knowledge public-id wrapper files.
  - [x] Use raw public ids directly.

- [x] Route platform addon discovery through the shared owner.
  - [x] Replace direct `getattr(config, "angee_addon", False)` in
    [platform/schema.py](/Users/alexis/Work/angee/angee-django/addons/angee/platform/schema.py:123)
    with `is_angee_addon`, unless platform truly needs a distinct named concept.

## Wave 3 — Backend Product-Fact Ownership

This should start after Agent B exposes shared GraphQL/domain helpers.

### Agent B Or Dedicated IAM Agent: IAM Overview

- [x] Move IAM overview aggregates and permission semantics to IAM backend.
  - [x] Identify every frontend-computed fact in
    [OverviewPage.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/iam/web/src/views/OverviewPage.tsx:89),
    [OverviewPage.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/iam/web/src/views/OverviewPage.tsx:105),
    and
    [OverviewPage.tsx](/Users/alexis/Work/angee/angee-django/addons/angee/iam/web/src/views/OverviewPage.tsx:312).
  - [x] Expose an IAM overview/dashboard GraphQL field or focused aggregate roots.
  - [x] Use Django ORM/Strawberry-owned backend aggregation where it fits; the
    overview's REBAC semantics and peek rows did not fit a pure
    `strawberry-django-aggregates` root.
  - [x] Use IAM/REBAC-owned resolver behavior for privileged grants and permission
    semantics.
  - [x] Update frontend to render returned facts, not infer from paginated rows.
  - [x] Add tests with data beyond page limits.

## Wave 4 — Documentation And Contract Cleanup

Run after code owners are settled.

### Agent F: Docs

- [x] Shrink `docs/composer.md`.
  - [x] Remove concrete API/default inventories that duplicate code contracts.
  - [x] Keep conceptual flow, owner map, and links to owning modules/classes.
  - [x] Ensure current seams such as `mcp_tools` and `http_mounts` are either
    described at their owning code docstrings or linked conceptually.

- [x] Update `docs/stack.md` only for real ownership changes.
  - [x] Add/lock `react-dropzone` only if the upload slice chooses it.
  - [x] Keep non-locked libraries out of implementation.

- [x] Add durable pitfalls/guidelines only when a new lesson is reusable.
  - [x] Prefer terse rules over prose inventories.

## Verification Matrix

Run scoped checks per slice, then broad checks before merge.

- [x] Backend/composer slices:
  - [x] `uv run examples/notes-angee/manage.py angee build`
  - [x] `uv run examples/notes-angee/manage.py angee build --check`
  - [x] `uv run examples/notes-angee/manage.py schema`
  - [x] `uv run examples/notes-angee/manage.py schema --check`
  - [x] Relevant `uv run pytest ...`

- [x] Frontend/codegen slices:
  - [x] `pnpm run codegen`
  - [x] `pnpm run typecheck`
  - [x] `pnpm run test`
  - [x] `pnpm run build`

- [x] UI/adoption slices:
  - [x] Component/unit tests for new base primitives.
  - [x] Addon composition tests after icon/status/i18n changes.
  - [x] Verify `/integrate/vcs` no longer throws the `integrate.VcsBridge` delete
    root metadata error after a fresh schema/codegen/Vite restart.
  - [x] Playwright e2e for affected pages if running stack is available.

- [x] Final drift check:
  - [x] No generated artifact drift except intentionally regenerated output.
  - [x] No new direct library dependency without stack/manifests/lock alignment.
  - [x] No remaining duplicated helper names called out by this plan, unless
    documented as an intentional exception.

## Suggested Agent Prompts

Use these as starting prompts for parallel workers. Each worker should be told it
is not alone in the codebase and must not revert changes made by other agents.

### Composer Worker

Fix Wave 1 / Agent A from `.agents/plans/dry-architecture-audit-fix-plan.md`.
Own only `angee/compose/**`, composer tests, and any needed composer docs.
Implement runtime drift ownership for `runtime/gql`, label validation, extension
order, and `AppGraph` duplicate/dependency semantics. Do not touch frontend or
GraphQL schema code except by coordinating first. Add focused tests and report
exact files changed.

### GraphQL Backend Worker

Fix Wave 1 / Agent B from `.agents/plans/dry-architecture-audit-fix-plan.md`.
Own `angee/graphql/**` plus backend addon schema call sites needed to adopt the
new helpers. Move admin-delete, action target resolution, IAM principal lookup,
integrate reverse-accessor lookup, and public ID conversion to their owners.
Add focused backend tests and report exact files changed.

### Typed GraphQL Worker

Fix Wave 1 / Agent C from `.agents/plans/dry-architecture-audit-fix-plan.md`.
Own authored-operation/codegen files and operator/agents web GraphQL boundary
files. Remove the handwritten agents variables type, add operator daemon typed
document codegen, split console-vs-daemon documents, and keep JSON scalar
runtime validation. Coordinate before touching shared base UI.

### Base UI Worker

Fix Wave 2 / Agent D from `.agents/plans/dry-architecture-audit-fix-plan.md`.
Own `packages/base/src/**` plus SDK model-metadata/selection files when fixing
data-view root metadata. Add shared inline create/rename, status-tone, date,
i18n, upload/list primitives as needed, and pin/fix the captured
`integrate.VcsBridge` `DataPage` delete-root runtime error. Keep addon changes to
compatibility shims only; leave adoption to the addon worker.

### Addon UI Worker

Fix Wave 2 / Agent E from `.agents/plans/dry-architecture-audit-fix-plan.md`
after the Base UI Worker lands primitives. Own addon web packages. Replace
AgentProvisioning duplication, inline controls, private status/date/public-id
helpers, and Platform/Resources i18n gaps. Do not modify base primitives except
for small bug fixes coordinated with the Base UI Worker.

### Docs Worker

Fix Wave 4 / Agent F from `.agents/plans/dry-architecture-audit-fix-plan.md`
after code contracts land. Own docs only. Shrink drifting inventories, update
stack ownership only when implementation changed ownership, and avoid duplicating
code contracts in prose.
