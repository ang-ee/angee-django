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
- [ ] Net production LOC trends downward across implementation slices. Guardrail
      tests do not count against the deletion signal; production growth is
      accepted only for unsafe-contract deletion or a shared owner that unlocks
      larger deletion.
- [ ] Every slice reports total regular tracked-file LOC versus baseline commit
      `261530412909d2d3864e83ba54dced7c9254083c`, not just local diff stats.

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
| S1 | bugfix | SDK write-state primitive | Make mutation loading follow the submitted promise and make `useBusyRun` overlap-safe. | frontend, architecture | LOC +3 prod; loading state stops depending on stale urql mutation flags. |
| E16 | 3.1 | DataToolbar picker shell | Share filter/group disclosure editor shell. | frontend, architecture | LOC -18; toolbar chrome has one owner. |
| E17 | 3.2 | `Filter` class | Move filter merge/AND algebra onto `Filter`. | frontend, architecture | LOC -38; pages call filter owner, no duplicate merge helpers. |
| E18 | 2.4 | `ExternalAccount`/`Credential` | Move provider/display projections to model properties. | backend, architecture | Production surface shrinks; schema fields become thin property dispatchers or annotations with safe optimizer metadata. |
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

## Higher-Level Analyzer Queue

These came from the June 20 read-only analyzer pass requested after E15. Treat
them as drawing-board candidates: each needs the slice gate before edits, and
P0/P1 items should run reviewers against the owner choice before implementation.

### Backend / Library Ownership

| ID | Priority | Owner candidate | Finding | Lower-surface target | Decision |
|---|---:|---|---|---|---|
| H1 | P1 | `agents.provisioning` service or `Agent` manager | Agent provisioning, rollback, secret sync, and daemon orchestration live in GraphQL resolvers. | Move provisioning/reprovision/deprovision work out of `agents/schema.py`; resolvers dispatch and return `ActionResult`. | Light architect approval on service vs manager shape. |
| H2 | P1 | Integration child lifecycle helper/manager | Integration, VCS bridge, and inference provider create/update repeat parent-field resolution, impl defaults, and patch handling. | One integrate-owned child lifecycle owner that accepts plain mappings/dataclasses, not Strawberry inputs. | Architect approval before setting future child pattern. |
| H3 | P2 | `angee.graphql.actions` + model methods | Action mutations repeat target lookup, `system_context`, and failure-to-`ActionResult` glue. | Small `run_action(...)` helper or model-owned action methods; keep permission/validation errors explicit. | No approval if kept small. |
| H4 | P2 | `django-zed-rebac` adapter | GraphQL permission guards repeat `current_actor`/`check_field_access` shapes. | Tiny `current_actor_can(...)` or parameterized `RebacObjectPermission`. | Approval if IAM/admin semantics change. |
| H5 | P2 | HTTP stack owner | OAuth, operator, GitHub, and webhooks carry parallel outbound HTTP/security behavior. | Decide stdlib helper vs locked HTTP dependency; preserve URL safety/IP pinning. | Stack-owner decision required. |
| H6 | P3 | Resource adoption owner | Resource loader interprets Django uniqueness/conditional constraints directly. | Prefer model-owned natural/adoption key hooks or import-export-native identity where exact. | Public resource semantics decision required. |

### Frontend / View Primitive Ownership

| ID | Priority | Owner candidate | Finding | Lower-surface target | Decision |
|---|---:|---|---|---|---|
| H7 | P0 | `@angee/base` rows + SDK authored hooks | Authored-query row pages repeat query/project/`RowsListView` wiring. | `AuthoredRowsView` or `useAuthoredRows` that owns state and list wiring while row projection/columns stay local. | API name/shape approval useful. |
| H8 | P0 | Operator-local first; maybe base remote collection later | Operator daemon sections are a parallel DataPage world. | `SnapshotSection`/`OperatorSnapshotRows` for snapshot slice, rows, href, empty/group wiring. | Decide operator-only vs base remote collection. |
| H9 | P1 | Base explorer/tree primitive | Storage and knowledge share tree/explorer page skeletons. | Declarative `TreeExplorerPage` shell with collection picker, selected route id, loading/not-found/drop hooks. | Architect approval. |
| H10 | P1 | SDK/base action runner | IAM/operator bespoke pending/error/refetch runners sit beside `useBusyRun`. | Shared action-state orchestrator; do not interpret every payload. | Approval if public cross-addon API. |
| H11 | P2 | `DataPage` drawer mode | Drawer DataPage control state is repeated in storage, knowledge, integrate templates. | Self-controlled `DrawerDataPage`/managed drawer mode. | No approval. |
| H12 | P2 | Base state fragments | List/graph error banners and dynamic labels drift from shared fragments/i18n. | Use shared `ErrorBanner`/state fragments and add small guard tests where useful. | No approval. |

### Naming / Architecture Forks

| ID | Priority | Owner candidate | Finding | Lower-surface target | Decision |
|---|---:|---|---|---|---|
| H13 | P0 | Integration child models | `Integration.impl_class` is stale parent-level implementation choice. | Parent `Integration` owns identity/lifecycle only; child model + child `backend_class`/`provider_type` own adapter kind. | Escalate if parent-only integrations remain real. |
| H14 | P0 | Integration lifecycle/health model | `Integration.status` mixes lifecycle and runtime health. | Split lifecycle from health/runtime status; bridge sync telemetry stops mutating lifecycle. | Escalate if `ERROR` is intentionally lifecycle. |
| H15 | P1 | `OAuthClient` vocabulary | OAuth “provider” route/page names collide with inference providers. | Code/routes use `oauthClient(s)`; UX labels may say provider if desired. | Escalate only if product vocabulary requires provider in code. |
| H16 | P1 | OAuth redirect policy | Frontend callback routing probes record shape and Anthropic strings. | Backend exposes callback/redirect policy; frontend consumes a field. | Escalate if browser must choose before start call. |
| H17 | P1 | VCS/source operation verbs | Import/discover/reconcile/refresh/sync are overloaded. | Normalize operation names around inventory, reconcile, refresh, sync bridge. | Escalate if `sync` is platform umbrella term. |
| H18 | P2 | Webhook API naming | Webhook filters still leak `impl_app`. | Rename to addon/source/integration-kind axis before API hardens. | Escalate if already external API. |
| H19 | P2 | Route/menu acronym convention | Route IDs use kebab acronym names while model/GraphQL use `MCP*`. | Use camel route/menu IDs like `agents.mcpServers`; URL paths may stay kebab. | Escalate if route names persisted. |
| H20 | P2 | Inference model metadata names | `model_use` is labeled Capability while `capabilities` is separate JSON. | Label as “Model use” or rename enum field consistently. | Escalate if domain wants capability everywhere. |

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
- [ ] E16 DataToolbar picker shell needs a broader no-growth design before
      implementation. A local `PickerDisclosure` extraction was rejected because
      it passed tests but grew the toolbar by 17 lines without enough payoff.
- [ ] If custom session-auth backend selection becomes a goal, lift the canonical
      OIDC login backend string to an IAM-owned constant; E7 intentionally keeps
      the current ModelBackend contract.
- [ ] Storage MIME catalogue glyph coverage: register every `icon_key` emitted by
      `010_storage.mimetype.yaml` and add a manifest/catalogue coverage test. E13
      only moved file-row glyph choice to `MimeType.icon_key` with a safe fallback.

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
| E5 Drop redundant Meta re-emission | same commit | -24 prod, +25 tests | composer emits fewer Django-owned facts | Django `Meta` inheritance owns `db_table`/`swappable`; composer keeps REBAC Meta re-emission only | backend + architecture pass | `ruff`; compose pytest; build check |
| E6 Use import-export result totals | same commit | -11 prod, +14 tests | resources load reports import-export-owned accounting | `Result.totals` owns created/updated/skipped row counts | backend + architecture pass | `ruff`; resources pytest |
| E7 Use Django login backend contract | same commit | -14 prod, +1 test | OIDC login returns a session-ready user | Django `login()` reads `user.backend`; no settings scan | backend + architecture pass | `ruff`; OIDC/IAM pytest |
| E8 Centralize OAuth client slug lookup | same commit | +3 prod, +56 tests | connect surfaces ask OAuthClient owner for provider-slug clients | `OAuthClientQuerySet.enabled_for_slug()` owns prod-first/fallback lookup | backend + architecture pass | `ruff`; focused connect pytest |
| E10 Move webhook delivery telemetry to model | same commit | +4 prod, +39 tests | webhook action delegates delivery telemetry to subscription | `WebhookSubscription.deliver_recorded()`/`deliver_test()` own delivery status recording | backend + architecture pass | `ruff`; focused webhook pytest |
| E11 Unify gated-read exposure guard | same commit | -3 | aggregate/history schema surfaces stop duplicating gated-read intersections | `angee.graphql.access.assert_no_gated_read_fields()` owns unsafe exposure rejection | backend + architecture pass | `ruff`; aggregate/GraphQL pytest |
| E12 Share `dedupeBy` utility | same commit | -10 source | base views/preferences reuse one first-wins dedupe mechanic without changing domain merge rules | `packages/base/src/lib/dedupe.ts` owns by-key array dedupe inside base | frontend + architecture pass | base typecheck; base vitest |
| E13 Use MIME catalogue file glyphs | same commit | -5 source | storage file rows carry catalogue glyph facts; views render row-owned icons | `MimeType.icon_key` owns file glyph choice; MIME checks only decide thumbnail rendering | frontend + architecture pass; registry coverage deferred | storage typecheck/test; `git diff --check` |
| E14 Add storage download cache contract | same commit | +18 prod, +63 tests | storage download response advertises validators and token-carrier-safe private caching | Django cache helpers own ETag/conditional/Vary mechanics; `content_hash` and token TTL supply facts | backend + architecture pass after fixes | `ruff`; storage pytest |
| E15 Use SDK model dumps | `d4811bd8` | -12 prod, +27 tests; total LOC `210,558` vs baseline `206,023` (`+4,535`) | provider addons stop recursively walking arbitrary objects for SDK JSON | OpenAI/Anthropic Pydantic SDK models own nested `model_dump(mode="json")`; tests use real SDK response models | backend + architecture pass | `ruff`; agents pytest + agents GraphQL pytest |
| S1 SDK mutation busy state | `02d5e096` | +3 prod, +82 tests, +44 plan; total LOC `210,686` vs baseline `206,023` (`+4,663`) | action/auth/resource callers keep using one SDK mutation seam; analyzer findings are queued as sliceable owner moves | `useDocumentMutation` uses `useBusyRun`; `useBusyRun` owns overlapping async busy state with a counter | frontend + architecture pass after P2 fix | SDK focused tests + full SDK test; SDK typecheck; base focused tests; `git diff --check` |
| E17 Move filter algebra to `Filter` | `31c1f2a3` | -8 prod, +44 tests, +2 plan; total LOC `210,722` vs baseline `206,023` (`+4,699`) | grouped list consumes data-view filter algebra instead of carrying local merge helpers | `Filter.combine()` / `Filter.and()` own bucket filter merge and object-shaped `AND`; `stableSerialize` has one shared owner | frontend + architecture pass | full base test; base typecheck; `git diff --check` |
| E18 Move connection display/projection facts to models | `44579351` | +80 prod, +122 tests, +1 document; production growth buys public/console projection split | public connected-account flows stop exposing console-rich OAuthClient/provider affordances; console fields dispatch to model-owned facts | `ExternalAccount`/`Credential`/`Integration` own display/provider labels; `Connected*` GraphQL types own public-safe API boundary | backend + architecture pass after fixes | connections/IAM/integrate focused pytest; ruff; schema/check; codegen; integrate web test/typecheck; host typecheck; `git diff --check` |
| E19 Move bridge sync lifecycle to `Bridge.run_sync()` | `3ca9c302` | -6 prod, +12 tests, +1 plan | scheduler/actions no longer hand-assemble mark/sync/record/error lifecycle | `Bridge.run_sync(now=...)` owns one sync attempt; callers own scope, counts, and messages | backend + architecture pass | scheduler/VCS pytest; focused integrate GraphQL; ruff; schema/check; `git diff --check` |
| E3 Share page-size prop sync hook | same commit | -3 prod, +1 plan | DataPage probe reuses the same data-view page-size sync as list/rows surfaces | `useSyncPageSize()` owns prop-to-state sync once inside base views | frontend + architecture pass | base focused test; base typecheck; `git diff --check` |
| H11 Add `DrawerDataPage` owner | same commit | -5 prod, +22 tests, +1 plan | storage, knowledge, and integrate template pages declare list/form facts without local drawer state | `DrawerDataPage` owns drawer-mode record state and inline control-band scoping | frontend + architecture pass | base DataPage test/typecheck; integrate/storage/knowledge tests/typecheck; host typecheck; `git diff --check` |
| S2 Share GraphQL WebSocket close policy | same commit | final LOC audit deferred; tests ignored | operator addon consumes the SDK transport close policy instead of carrying a duplicate close-code list | SDK GraphQL transport owns fatal close-code classification; service log stream keeps its explicit normal-close reconnect difference | frontend + architecture pass | SDK/operator test/typecheck; `git diff --check` |
| S3 Reuse SDK error-message owner | same commit | final LOC audit deferred | base, agents, and integrate surfaces stop re-deriving Error-or-fallback copy | `@angee/sdk/errorMessage` remains the single generic caught-value message owner; richer ACP/validation parsers stay local | frontend + architecture pass | base/integrate/agents typecheck + tests; `git diff --check` |
| S4 Delegate resource tier normalization | same commit | final LOC audit deferred; tests ignored | resource manifests stop carrying a duplicate tier validator | `ResourceTier.from_value()` owns enum/string normalization and error text for every resource tier caller | backend + architecture pass | focused resources pytest; ruff; `git diff --check` |
| S5 Move daemon action toast sink to owner | same commit | final LOC audit deferred; tests ignored | operator action hooks pass the shared toast sink instead of re-declaring message adapters | `runDaemonAction()` owns failure-message toast surfacing for daemon actions | frontend + architecture pass | operator typecheck/test; `git diff --check` |
| S6 Share base avatar initials owner | same commit | final LOC audit deferred; tests ignored | user/owner widgets reuse avatar-owned initials instead of carrying duplicate word-splitting helpers | `avatarInitials()` owns two-word avatar initials; `UserMenu` keeps its different one-word fallback | frontend + architecture pass | base typecheck/test; `git diff --check` |
| S7 Delegate OAuth discovery target lookup | same commit | final LOC audit deferred; tests ignored | OAuth endpoint discovery stops manually resolving action target public IDs | `resolve_action_target()` owns elevated action lookup and not-found errors; discovery/save stays under action context | backend + architecture pass | focused IAM/action pytest; ruff; `git diff --check` |
| S8 Share SDK row public-id owner | same commit | final LOC audit deferred; tests ignored | base and addon views stop rechecking `row.id` string shape for public record IDs | `rowPublicId()` owns resource-row public ID extraction; table surfaces keep their explicit non-resource fallback keys | frontend + architecture pass | SDK/base/addon tests + typecheck; `git diff --check` |
| S9 Delegate knowledge GraphQL ID lookups | same commit | final LOC audit deferred; tests ignored | knowledge custom mutations stop hand-rolling public-ID lookup and not-found errors | `require_instance_for_id()` owns GraphQL public-ID coercion for vault/page inputs; managers keep create/write authorization | backend + architecture pass | knowledge pytest; ruff; `git diff --check` |
| S10 Delegate integrate admin target lookups | same commit | final LOC audit deferred; tests ignored | external-account and credential admin mutations stop hand-rolling elevated public-ID lookup | `resolve_action_target()` owns admin action target lookup and not-found errors; mutation bodies keep transaction/write/reveal contexts | backend + architecture pass | focused IAM GraphQL/action pytest; ruff; `git diff --check` |
| S11 Reuse record action ID owner | same commit | final LOC audit deferred; tests ignored | integrate custom record actions stop inspecting action-context records by hand | `recordActionId()` owns saved-record action id extraction; callbacks keep their prompt/redirect mutation behavior | frontend + architecture pass | base/integrate tests + typecheck; `git diff --check` |
| S12 Delegate IAM user update ID lookup | same commit | final LOC audit deferred; tests ignored | IAM user update stops hand-rolling public-ID lookup and not-found errors | `require_instance_for_id()` owns GraphQL user public-ID lookup; update mutation keeps admin write transaction/context | backend + architecture pass | focused IAM GraphQL pytest; ruff; `git diff --check` |
| S13 Delegate GraphQL internal ID lookups | same commit | final LOC audit deferred; tests ignored | deletion/revision internals stop importing base public-ID lookup directly | `angee.graphql.ids` owns GraphQL public-ID lookup/not-found helpers; revisions preserve actor-hidden empty-list behavior | backend + architecture pass | CRUD/knowledge/revisions pytest; ruff; `git diff --check` |
| S14 Reuse SDK row ID in graphcache | same commit | final LOC audit deferred; tests ignored | SDK graphcache no longer redefines public row ID extraction | `rowPublicId()` owns string public-id extraction for resource rows and normalized cache keys | frontend + architecture pass | SDK cache/resource tests + typecheck; `git diff --check` |
| S15 Move OAuth public-ID lookup to schema | same commit | final LOC audit deferred; tests ignored | OAuth flow module stops carrying GraphQL public-ID lookup helpers | integrate schema owns `PublicID` lookup through `resolve_action_target()`; OAuth flow keeps browser/session/sqid plumbing | backend + architecture pass after stale reason fix | focused integrate/IAM GraphQL pytest; ruff; `git diff --check` |
| S16 Move data-view favorite shape to model | same commit | final LOC audit deferred; tests ignored | Data-view context stops validating saved-favorite records and allocating favorite IDs | `DataViewState`/data-view model own favorite decoding and ID allocation; context remains the browser-storage adapter | frontend + architecture pass after storage-read fix | base model/DataPage tests; base typecheck; `git diff --check` |
| S17 Share connect OAuth-client hint resolution | same commit | final LOC audit deferred; tests ignored | integration and inference-provider connect mutations stop duplicating hint-to-client lookup/error plumbing | `integrate.connect.enabled_oauth_client_from_hint()` owns backend hint formatting, elevated lookup, and typed OAuth error text | backend + architecture pass after provider vendor-slug fix | focused integrate/agents connect pytest; ruff; `git diff --check` |
| S18 Reuse base record path owner | same commit | final LOC audit deferred; tests ignored | storage and knowledge pages stop hand-building encoded detail URLs | base `recordPath()` owns record-id URL encoding for routed views and addon detail links | frontend + architecture pass | base/storage/knowledge typecheck; storage/knowledge tests; `git diff --check` |
| S19 Move group-option merge to list-view utils | same commit | final LOC audit deferred; tests ignored | `ListView` stops carrying the last local by-id toolbar option merge | `list-view-utils.mergeById()` owns filter/group toolbar option merging once | frontend + architecture pass after stale-import fix | base typecheck/test; `git diff --check` |
| S20 Share GraphQL action target context | same commit | +25 prod, +42 tests; final total audit deferred | integrate/agents action mutations stop carrying the repeated resolve-target-then-enter-action-context boundary | `angee.graphql.actions.action_target()` owns the elevated GraphQL action boundary; mutation bodies keep result/failure semantics | backend + architecture pass; growth accepted for security-sensitive owner move | focused GraphQL action tests; ruff; `git diff --check` |
| S21 Delete dead operator section title props | same commit | -4 prod; final total audit deferred | operator runtime list sections stop exporting ignored compatibility-only `title` props | route chrome owns top-level section headings; live embedded title props remain only where rendered | frontend notes intentional public type break; architecture pass | operator typecheck/test; `git diff --check` |
| S22 Delete legacy IAM login callback alias | same commit | final LOC audit deferred; tests ignored | IAM web addon stops mounting `/login/callback` beside the canonical `/sso/callback` login route | `LOGIN_CALLBACK_PATH` owns the single login callback route; integrate `/callback` remains a provider-specific OAuth connect alias | frontend + architecture pass after plan wording fix | IAM web typecheck/test; route grep; `git diff --check` |
| S23 Reuse base status owners in operator state tags | same commit | final LOC audit deferred; tests ignored | operator `StateTag` stops carrying private status tone and slug-label helpers | base `statusTone(..., { unknownTone: "neutral" })` and `statusLabel()` own status coloring/casing | frontend + architecture pass | operator typecheck/test; `git diff --check` |
| S24 Share operator row action renderer | same commit | final LOC audit deferred; tests ignored | service/workspace/source action modules stop each rendering the same button map | operator `RowActions` owns row-action button rendering; domain hooks own action definitions and confirmations | frontend + architecture pass | operator typecheck/test; `git diff --check` |
| S25 Reuse public-id lookup owners in custom flows | same commit | final LOC audit deferred; tests ignored | storage custom GraphQL mutations and OAuth session flow stop hand-filtering sqids | `angee.graphql.ids.instance_for_id()` owns GraphQL public-id resolution; `instance_from_public_id()` owns scoped OAuth public-id lookup | backend + architecture pass; tiny deletion accepted for public-id boundary cleanup | storage/OIDC/account-connect pytest; ruff; schema check; `git diff --check` |
| S26 Move OAuth flow display message to error owner | same commit | final LOC audit deferred; tests ignored | integrate, OIDC, and agents schemas stop carrying local `provider_message or str(error)` copies | `OAuthFlowError.public_message` owns safe provider-message fallback text | backend + architecture pass | OAuth/connect/OIDC/agents focused pytest; ruff; schema check; `git diff --check` |
| S27 Delete OAuth connect alias wrappers | same commit | final LOC audit deferred; tests ignored | agents and integrate pages stop exporting local wrappers around connect visibility/callback helpers | integrate `canConnectRecord()` and `connectCallbackPathForRecord()` own connect affordance visibility and callback alias choice | frontend + architecture pass; wrapper deletion accepted | agents/integrate focused tests + typecheck; `git diff --check` |
| S28 Share agents record-state helpers | same commit | final LOC audit deferred; tests ignored | agent page and provisioning panel stop duplicating boundary string/state normalization | agents-local `agent-record` helper owns agent lifecycle/runtime/string field reads | frontend + architecture pass; agents-local owner accepted | agents focused test/typecheck; `git diff --check` |
| S29 Share CRUD mutation clone behavior | same commit | final LOC audit deferred; tests ignored | Angee create/update mutation fields stop duplicating Strawberry clone attribute preservation | local `_AngeeMutationCloneMixin` owns key/argument preservation for CRUD mutation fields | backend + architecture pass; optional clone guard added | CRUD/knowledge pytest; ruff; schema check; `git diff --check` |
| S30 Share widget option text-label owner | same commit | final LOC audit deferred; tests ignored | combobox, many-to-many, and owner cell widgets stop duplicating text-label coercion | `widgets/types.optionTextLabel()` owns text-only option label coercion beside `optionLabel()` | frontend + architecture pass after combobox fallback fix | base test suite; base typecheck; `git diff --check` |
| S31 Delegate page-editor autosave debounce | same commit | final LOC audit deferred; tests ignored | knowledge page editor stops owning raw timeout/pending-body refs for autosave | `useDebouncedCallback` owns autosave scheduling and unmount flush; page editor owns save payload/status | frontend + architecture pass | knowledge tests; knowledge typecheck; `git diff --check` |
| S32 Compose storage file-drop target | same commit | final LOC audit deferred; tests ignored | storage file browser stops hand-owning native file drag state and overlay rendering | base `UploadDropTarget` owns native file-drop filtering, drag depth, copy effect, overlay state, and disabled-drop default suppression | frontend + architecture pass after disabled-drop fix | storage test/typecheck; base drop-target test suite/typecheck; `git diff --check` |
| S33 Delete storage AdminTable wrapper | same commit | final LOC audit deferred; tests ignored | storage settings composes `DrawerDataPage` directly instead of a forwarding local wrapper | base `DrawerDataPage` owns drawer record state and inline control-band behavior | frontend + architecture pass | storage test/typecheck; base DataPage test suite; `git diff --check` |
| S34 Share SDK client class import owner | same commit | final LOC audit deferred; tests ignored | OpenAI and Anthropic inference backends stop duplicating lazy SDK import wrappers | `SDKInferenceBackend.client_class_path` owns dotted client import and install-hint errors while provider backends declare only paths | backend + architecture pass; client override guard added | focused agents pytest; ruff; `git diff --check` |
| S35 Share addon contribution loader | same commit | final LOC audit deferred; tests ignored | URLConf and ASGI discovery stop duplicating Angee-addon gating, conventional submodule import, callable handling, and iterable validation | `angee.addons.addon_contribution()` owns conventional addon contribution loading | backend + architecture pass; negative loader coverage added | focused settings pytest; ASGI pytest; ruff; `git diff --check` |
| S36 Delete repeated AppConfig auto-field facts | same commit | -13 prod; final total audit deferred | addon manifests stop restating the project-wide Django primary-key default | `angee.compose.defaults.DEFAULT_AUTO_FIELD` owns the composed Django default; AppConfig classes keep addon facts only | backend + architecture pass | settings/apps pytest; build check; migration dry-run; ruff; `git diff --check` |
| S37 Delegate storage CRUD actions to SDK builder | same commit | -51 prod, +1 focused test; final total audit deferred | storage file/folder hooks stop carrying authored standard CRUD documents and declare only storage-specific verbs | SDK `useResourceMutation()` / mutation document builder own create/update/delete documents; storage hooks own action intent and delete confirmation | frontend + architecture pass | codegen; storage test/typecheck; host typecheck; `git diff --check` |
| S38 Delegate integration impl-key coercion to model | same commit | final LOC audit deferred | integrate schema stops reaching into `ImplClassField` for parent integration keys | `impl_key_for()` owns enum/string key canonicalization, blank/default handling, and registry validation; schema only normalizes GraphQL `UNSET` | backend + architecture pass | focused integrate GraphQL pytest; ruff; `git diff --check` |
| S39 Delegate knowledge CRUD actions to SDK builder | same commit | -39 prod, +1 focused test; final total audit deferred | knowledge page hooks stop carrying authored standard CRUD documents; unused page-action vault create hook is deleted | SDK `useResourceMutation()` owns page create/update/delete documents and result extraction; knowledge keeps markdown body mutation authored | frontend + architecture pass | codegen; knowledge test/typecheck; host typecheck; `git diff --check` |
