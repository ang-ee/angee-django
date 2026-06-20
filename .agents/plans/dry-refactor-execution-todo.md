# DRY Refactor Execution Todo

**Goal:** finish the pre-1.0 DRY refactor as shippable, reviewed, committed
slices. This is an execution index over the source plans; it must not reopen
completed plans, merge unrelated owners, or let mechanical checkbox work pass as
architecture.

Source plans:

- `.agents/plans/post-dry-audit-cleanup-findings.md`
- `.agents/plans/typed-graphql-operations.md`
- `.agents/plans/mcp-over-graphql.md`
- `.agents/plans/react-consistency-todo.md`
- `.agents/plans/addon-page-dsl-dry-slices.md`
- `.agents/plans/view-composition-drift-audit.md`
- `.agents/plans/reviewer-slicing-strategy.md`
- `.agents/plans/library-leverage-research-checklist.md`

## Slice Gate

Every implementation slice must be one owner, one concern, one question. Before
editing, write the reviewer brief in the work notes or PR/commit closeout:

```text
Slice:
Owner under review:
Concern:
Question:
In scope paths:
Out of scope paths:
Sibling patterns to compare:
Library/docs to check:
Expected deletion signal:
Escalation trigger:
Output file:
```

Reviewers must also answer the drawing-board questions from
`view-composition-drift-audit.md`:

- [ ] What would we build if this did not exist yet?
- [ ] Which current abstractions would disappear?
- [ ] Which dependency or Angee owner should carry the behavior?
- [ ] Is the true owner outside this repository?
- [ ] How many routes/files/classes/hooks/schema roots/settings would be deleted
      or merged?
- [ ] What compatibility or migration cost blocks deletion?
- [ ] Does the lower-surface option need human architect approval?

Per-slice implementation gate:

- [ ] Pin behavior with focused tests when deleting or moving code.
- [ ] Implement only the named owner-moving change.
- [ ] Run slice tests plus `ruff`/typecheck/schema checks that cover the owner.
- [ ] Run reviewers before commit:
  - backend/Django reviewer for Python/model/GraphQL/resource changes;
  - frontend/React reviewer for TypeScript/UI/SDK changes;
  - architecture/DRY reviewer for every slice.
- [ ] Fix all blocker findings.
- [ ] Record deferred non-blockers in this file or the owning plan.
- [ ] Commit the slice alone with a clean tree.
- [ ] Update the scoreboard with LOC, addon shape, owner shape, reviewers, and
      verification.

## Success Looks Like

- [ ] Addons declare facts only: models, fields, forms, routes, menus, actions,
      provider/backend choices, and exceptional copy.
- [ ] Backend resolvers dispatch to models/managers/querysets/library owners.
- [ ] Frontend pages compose `DataPage`/`ListView`/`RowsListView`/`FormView`
      and shared state surfaces rather than local tables/forms/filter glue.
- [ ] Locked dependencies in `docs/stack.md` own their native concerns.
- [ ] Names are normalized across model, GraphQL, TypeScript, route, menu, file,
      and docs.
- [ ] Net LOC trends downward across implementation slices. Growth is accepted
      only for guardrail tests, unsafe-contract deletion, or a shared owner that
      unlocks larger deletion.

## Closed Workstreams

Do not reopen these unless new evidence contradicts the source plan.

| Workstream | Status | Evidence | Success state |
|---|---|---|---|
| GraphQL sqid public IDs | Done | `3502d73c`, `b26257d4` | raw sqids, `abc_...` prefixes, no unsafe generic `node(id:)` root |
| React consistency main pass | Done | `react-consistency-todo.md` marks T1/T3/T5/T6/T15/T16/T17 done and final drift greps clean | design-system spine and i18n decision are closed; only T19 speculative fragments remain deferred |
| Addon page DSL D1-D6 | Done | `addon-page-dsl-dry-slices.md` resume state says all slices done/reviewed | notes addon is declarative; chrome/labels/options/filters/groups/revisions are owner-derived |
| MCP FastMCP v2 + GraphQL engine/notes conversion | Partially done | `mcp-over-graphql.md` phasing A and B1 engine done | only B1.x/B2 deferred items remain open below |

## Ready Queue From Post-DRY Audit

Execute these cheapest high-confidence deletions first unless a newer reviewer
finds an earlier wrong fork.

| ID | Source | Owner | Action | Reviewers | Success |
|---|---|---|---|---|---|
| E1 | 6.3 | resources/constants | Delete dead `FROZEN_TIERS`. | backend, architecture | LOC -2; resources tiers remain declared by live constants only. |
| E2 | 3.5 | `groupFieldLabel` owner | Import/use existing owner in DataToolbar. | frontend, architecture | LOC -4; toolbar stops re-deriving group labels. |
| E3 | 3.6 | page-size sync hook | Extract byte-identical page-size effect to `useSyncPageSize`. | frontend, architecture | LOC -9; list surfaces share one sync primitive. |
| E4 | 1.5 | Anthropic/OpenAI SDK pages | Delete `_iter_page`; iterate SDK pages directly. | backend, architecture | LOC -8; provider addons rely on SDK pagination and multi-page sync is tested. |
| E5 | 5.1 | Django/composer | Drop dead `db_table` / redundant `swappable` re-emission. | backend, architecture | LOC -22; runtime generation relies on Django defaults where equivalent. |
| E6 | 6.1 | django-import-export | Replace `LoadResult.from_rows` tallying with native result totals. | backend, architecture | LOC -18; resources code reports import-export-owned accounting. |
| E7 | 1.1 | Django auth | Replace `_session_backend` settings scan with `user.backend`/login contract. | backend, architecture | LOC -14; OIDC code no longer searches backend strings. |
| E8 | 1.4 | OAuthClient manager | Unify enabled OAuth-client-by-slug lookup and error contract. | backend, architecture | LOC -8; integrate/agents ask one manager owner. |
| E10 | 2.3 | `WebhookSubscription` | Add `deliver_test()` and delete resolver duplicate delivery logic. | backend, architecture | LOC -3 plus correctness; failed test deliveries record real status. |
| E11 | 7.1 | gated-field guard owner | Unify gated-field guard. | backend, architecture | LOC -8; one owner rejects unsafe gated aggregate/history exposure. |
| E12 | 3.3 | base lib utility | Add/use `dedupeBy(items, keyOf)` where clearer. | frontend, architecture | LOC -16; repeated seen-set loops collapse without awkward call sites. |
| E13 | 3.7 | file glyph catalogue | Derive file glyph from catalogue owner. | frontend, architecture | LOC -7; file views stop carrying local icon maps. |
| E14 | 8.1 | storage download response | Add/download ETag and Cache-Control owner behavior. | backend, architecture | LOC may grow +6; HTTP caching contract becomes explicit and tested. |
| E15 | 1.3 | SDK model serialization | Replace `_json_value` recursion with SDK/Pydantic `model_dump(mode="json")`. | backend, architecture | LOC -30; provider glue stops walking arbitrary objects. |
| E16 | 3.1 | DataToolbar picker shell | Share filter/group disclosure editor shell. | frontend, architecture | LOC -18; toolbar chrome has one owner. |
| E17 | 3.2 | `Filter` class | Move filter merge/AND algebra onto `Filter`. | frontend, architecture | LOC -38; pages call filter owner, no duplicate merge helpers. |
| E18 | 2.4 | `ExternalAccount`/`Credential` | Move provider/display projections to model properties. | backend, architecture | LOC -8; schema fields become property dispatchers. |
| E19 | 2.2 | `Bridge` | Add `Bridge.run_sync(*, now)` lifecycle owner. | backend, architecture | LOC -10; scheduler/actions share one sync attempt primitive. |
| E20 | 5.2 | history binding | Replace direct simple-history binding with `HistoryMixin` if it reduces surface. | backend, architecture | LOC neutral; source models use one history owner. |
| E21 | 9.2 | SDK schema metadata boot | Parse SDL once at boot if still needed after typed-GraphQL work. | frontend, architecture | LOC 0..-5; schema metadata boot does one type-graph build. |
| E22 | 1.2 | django-zed-rebac | Move REBAC `order_by` translation upstream before deleting IAM branch. | backend, architecture | LOC -15 in repo after upstream change; storage modes share one ordering API. |
| E23 | 6.2 | graphlib/toposort | Consider native topo sort only if stability concerns are solved. | backend, architecture | LOC -10 if accepted; resource ordering stays deterministic. |
| E24 | 4.2 | TanStack Form listeners | Replace `afterFieldChange` workaround with form listeners if recursion risk is closed. | frontend, architecture | LOC -10; form owner handles field-change effects. |
| E25 | 4.1 | form primitive layer | Decide whether `useRecordForm` extraction belongs in base. | frontend, architecture | LOC neutral; only implement after layer choice approval. |
| E26 | 2.1 | GraphQL CRUD/model defaults | Collapse Integration/VcsBridge mutations onto `crud()` where exact. | backend, architecture | LOC -40..-55; schema declares facts, CRUD/model owners handle plumbing/defaults. |
| E27 | 9.1 | GraphQL codegen metadata | Move SDL metadata to build-time codegen after metadata contract is proven. | frontend, backend, architecture | bundle/CPU win; runtime SDK stops deriving large metadata repeatedly. |

Covered, not queued:

- Post-audit 3.4 GroupedList i18n sweep is covered by the closed React
  consistency/i18n pass. Do not reopen unless a new drift grep or reviewer finds
  concrete remaining hardcoded copy.

## Larger DRY Workstreams

These have their own source plans. Split each into sub-slices using the required
reviewer brief before implementation.

### G1. Typed Authored GraphQL Operations

Source: `typed-graphql-operations.md`.

Todo:

- [ ] Add/confirm `docs/stack.md` ownership for GraphQL Code Generator.
- [ ] Generate typed authored operation documents per schema.
- [ ] Type `useAuthoredQuery`, `useAuthoredMutation`, and `useAuthoredSubscription`
      over `TypedDocumentNode` while keeping low-level runtime string runners.
- [ ] Delete hand-written `...Data` / `...Variables` interfaces that duplicate SDL.
- [ ] Keep runtime JSON validators where generated `JSON` remains `unknown`.

Success:

- **LOC:** authored GraphQL interfaces/generics deleted; codegen config growth is justified.
- **Addon shape:** documents state field selections only.
- **Owner shape:** emitted SDL + codegen own operation types.

### G2. MCP GraphQL Tool Layer Completion

Source: `mcp-over-graphql.md`.

Todo:

- [ ] Finish B1.x: `fixed`/`args`, `CustomTool`, examples, build-time validation,
      collisions, and union/interface rejection.
- [ ] Generate/sync `MCPTool` catalogue rows from specs.
- [ ] Use `Agent.mcp_tools` as the per-agent allow-list.
- [ ] Keep execution under actor-scoped GraphQL; no duplicated permission/query logic.

Success:

- **LOC:** hand-rolled MCP ORM/tool code disappears as specs replace it.
- **Addon shape:** addons expose curated GraphQL operations as tool facts.
- **Owner shape:** GraphQL owns semantics; FastMCP owns protocol mechanics.

### G3. View Composition Drift Follow-Up

Source: `view-composition-drift-audit.md`, `knowledge-page-decomposition.md`.

Todo:

- [ ] Do not reopen completed addon-page DSL D1-D6.
- [ ] If knowledge decomposition resumes, split base lifts first:
      `TreeView`/`FolderTree`, `StatusBar`, then knowledge-specific views.
- [ ] Apply drawing-board review before adding page-local components.

Success:

- **LOC:** knowledge addon TSX shrinks or stays declarative as richer views land.
- **Addon shape:** knowledge declares views/panels; base owns reusable explorer/status primitives.
- **Owner shape:** storage-explorer/DataPage archetype remains the page owner.

### G4. React Speculative Fragment Decision

Source: `react-consistency-todo.md` T19.

Todo:

- [ ] For each storybook-only fragment, decide on first real consumer: delete,
      keep storybook-only, or promote to a live base primitive.
- [ ] Do not run a broad design-system pass; the main React consistency pass is closed.

Success:

- **LOC:** unused fragments are deleted when no consumer appears.
- **Addon shape:** addons use live primitives only.
- **Owner shape:** storybook examples do not masquerade as platform API.

## Deferred / Human Decision Queue

- [ ] Upstream `django-zed-rebac` order-by translation scope for E22.
- [ ] `useRecordForm` layer choice before E25.
- [ ] Graphlib topo-sort stability before E23.
- [ ] Whether G3 knowledge decomposition is in this DRY wave or a product feature wave.
- [ ] Whether E27 build-time metadata waits for G1 typed operations.

## Running Scoreboard

Update after every slice commit.

| Slice | Commit | LOC | Addon shape result | Owner shape result | Reviewer result | Verification |
|---|---:|---:|---|---|---|---|
| S0 GraphQL sqid IDs | `3502d73c` | +824 incl. plan/tests | raw public IDs, no Relay wrappers | public-id helpers + typed details own identity | pass | pytest/schema/typecheck |
| S0 sqid separator | `b26257d4` | +70 | canonical `abc_...` sqids | `SqidField` owns separator | pass | pytest/schema/ruff |
| E1 Delete `FROZEN_TIERS` | same commit | -3 | resources addon keeps only live tier facts | resource skip behavior remains owned by `_skip_decision`/ledger hash logic | backend + architecture pass | `ruff`; resources pytest |
| E2 Reuse `groupFieldLabel` owner | same commit | -4 source | toolbar composes shared list label owner | `ListInternals.groupFieldLabel` remains the only group-field casing rule | frontend + architecture pass | base typecheck/test |
| E3 Extract `useSyncPageSize` | same commit | -8 source | list surfaces share page-size sync instead of duplicating effects | `data-view-surface` owns URL/page-size synchronization once | frontend + architecture pass | base typecheck/test |
| E4 Native SDK page iteration | same commit | -9 prod, +32 tests | provider addons iterate SDK pages directly and sync later-page models | OpenAI/Anthropic SDK `SyncPage.__iter__` owns pagination | backend + architecture pass | `ruff`; agents pytest |
