# Post-DRY-Audit Cleanup Findings

**Parent context:** `.agents/plans/pre-1-0-dry-audit-research-findings.md`,
`.agents/plans/refactoring-workflow.md`, `.agents/plans/reviewer-slicing-strategy.md`

These are 27 NEW, verified-novel cleanup opportunities surfaced after the
pre-1.0 DRY audit. Each was confirmed REAL (the cited shape exists) and NOVEL
(not already proposed in the react-consistency, typed-graphql, mcp, integration,
or composer plans, nor in the COVERED digest). This is a decision queue, not an
implementation plan — no product code was changed and no tests were run while
producing it.

Every entry names its **owner** per the Architecture Gate: a locked library
(`docs/stack.md`), an Angee primitive, or the class/file that owns the fact. LOC
deltas are the verifier's honest re-estimates (often below the original claim);
where the win is correctness/testability/bundle rather than deletion, that is
stated.

---

## Highest-leverage shortlist

| Title | Theme | Est LOC | Confidence |
|---|---|---|---|
| SDL→metadata derivation moved to build-time codegen | SDK metadata | -60 (bundle/CPU) | medium |
| FilterPicker disclosure editors share one card shell | Base UI thinning | -38 | medium |
| Filter combine/merge algebra onto the `Filter` class | View decomposition | -38 | medium |
| `_json_value` → Pydantic `model_dump(mode="json")` | Backend lib leverage | -30 | medium |
| Composer drops dead `db_table`/redundant `swappable` re-emit | Composer | -22 | high |
| `LoadResult.from_rows` → import-export `Result.totals` | Resources | -18 | high |
| Dedupe-by-key loops → one `dedupeBy(items, keyOf)` util | Base UI thinning | -16 | medium |
| REBAC order_by storage-mode branch → library translation | Backend lib leverage | -15 | high |
| `_session_backend` substring scan → `user.backend` | Backend lib leverage | -14 | high |
| `usePageEditor` autosave → `useDebouncedCallback` | Frontend lib leverage | -12 | high |

---

## Theme 1 — Backend library leverage

Concerns owned by a locked library or the framework auth/REBAC contract that the
code re-derives by hand.

### 1.1 `_session_backend` substring scan → Django `user.backend` — high, -14
- **Owner:** Django auth contract (`docs/stack.md`). The backend identity belongs
  on the user the OIDC resolver mints, or one named constant.
- **Files:** `addons/angee/iam_integrate_oidc/schema.py:179,251`,
  `addons/angee/iam_integrate_oidc/identity.py:243`, `addons/angee/iam/autoconfig.py:16`
- **Change:** Set `user.backend` to the canonical ModelBackend dotted path where
  the resolver creates/loads the login user, call `auth_login(request, user)` with
  no explicit `backend=`, and delete `_session_backend` plus its `settings` import.
  The env has exactly two backends, so the "first path without 'rebac'" heuristic
  is a brittle re-derivation of a known constant.
- **Guardrail:** Login e2e/test asserting the session's `_auth_user_backend` is
  ModelBackend after an OIDC link login.

### 1.2 REBAC order_by storage-mode branch → library order translation — high, -15
- **Owner:** django-zed-rebac (`docs/stack.md`: relationship storage). The
  registry queryset already translates `filter`/`exclude`/`get`; only `order_by`
  is untranslated, which is the sole reason IAM forks.
- **Files:** `addons/angee/iam/schema.py:333,349`
- **Change:** Drop `_relationship_storage_lookups()` and the `_meta.fields`
  sniff. Have rebac translate `order_by` (or expose canonical ordering field
  names) and call against that one contract. IAM keeps only its `include_relation`
  choice. **Gated on an upstream rebac change** (~5-8 lines added there).
- **Guardrail:** Permission-hub list test that runs green under both
  `REBAC_LOCAL_BACKEND_STORAGE` modes (registry + denormalized).

### 1.3 `_json_value` → Pydantic `model_dump(mode="json")` — medium, -30
- **Owner:** anthropic / openai typed SDK models (Pydantic v2). `model_dump(mode=
  "json")` owns "typed model → JSON-safe nested dict", including nested models/enums.
- **Files:** `addons/angee/agents/sdk_backends.py:212-249`,
  `addons/angee/agents_integrate_anthropic/backend.py:84-89`,
  `addons/angee/agents_integrate_openai/backend.py:94-99`
- **Change:** Replace the `_json_object`/`_json_list`/`_json_value` family with
  `value.model_dump(mode="json")` at the backend boundary (guard `None`); drop the
  Mapping/Sequence/`__dict__`-scraping recursion that exists only for test doubles.
  Switch agents tests to real SDK types (or thin `model_dump`-exposing objects).
- **Guardrail:** Backend test asserting `record_inference` persists the same JSON
  for a real `Message`/`completion` as today (absorb `model_dump`'s fuller keys).

### 1.4 OAuth-client-by-slug resolution unified (agents + integrate) — high, -8
- **Owner:** `OAuthClientManager`/`OAuthClientQuerySet`
  (`addons/angee/integrate/models.py:94-113`) already owns OAuthClient read scopes.
- **Files:** `addons/angee/agents/schema.py:503-515`,
  `addons/angee/integrate/schema.py:531-549`
- **Change:** Add one `enabled_for_slug(slug)` queryset method (prod-first, ordered
  fallback, must be enabled); both helpers call it, keeping only per-caller hint
  extraction. Normalize the two drifting error codes
  (`provider_not_connectable` vs `integration_not_connectable`) into one connect
  contract.
- **Guardrail:** Test both connect paths raise the same `OAuthFlowError` code when
  no enabled client exists for the slug.

### 1.5 SDK page iteration → native SyncPage auto-pagination — high, -8
- **Owner:** anthropic / openai SDK `SyncPage` (`__iter__` auto-paginates;
  `docs/stack.md` model-catalogue rows).
- **Files:** `addons/angee/agents/sdk_backends.py:179-186`,
  `addons/angee/agents_integrate_anthropic/backend.py:46`,
  `addons/angee/agents_integrate_openai/backend.py:60`
- **Change:** Delete `_iter_page` and iterate the page directly
  (`for model in client().models.list(limit=...)`). Update the two test doubles to
  return an iterable, removing the `.data`/`list(page)` fork. Also fixes a latent
  truncation bug: multi-page catalogues are currently silently cut to page one.
- **Guardrail:** Backend test with a fake page that yields across two pages,
  asserting all models sync.

---

## Theme 2 — Integrate core decomposition

`Bridge`/`WebhookSubscription`/`ExternalAccount` own data the resolvers decode
from outside. Put the behavior on the owning class; resolvers become thin
dispatchers. Order by value within the theme.

### 2.1 Integration/VcsBridge create+update mutations → `crud()` FK plumbing — medium, -40..-55
- **Owner:** `angee.graphql.crud.crud()` already resolves every FK PublicID and
  skips None/UNSET via `coerce_relation_public_ids` (`angee/graphql/crud.py:135-145`,
  `angee/graphql/ids.py:85-106`); `_SOURCE_MUTATION` proves the native path.
- **Files:** `addons/angee/integrate/schema.py:1279-1336,1728-1845,1857-1866`
- **Change:** Replace the three resolvers with `crud(...)`; move the genuinely
  custom impl-key canonicalization into the model's `clean()`/`save()` or
  `ImplClassField`. **Caveat:** `update_vcs_bridge` re-materializes defaults only
  when the impl key changes (a GraphQL-derived "provided" set) — logic `crud()`'s
  update path does not perform, so it may retain a thin hook. Do NOT import GraphQL
  sentinels into the base layer.
- **Guardrail:** Mutation tests for create + update (including backend-change
  re-seed) over a PublicID FK input.

### 2.2 `Bridge.run_sync(*, now)` owns the mark/sync/record triad — medium, -10
- **Owner:** `Bridge` (`addons/angee/integrate/models.py:1206,1232-1273`) — it
  exposes the three primitives separately but no method bracketing one attempt.
- **Files:** `addons/angee/integrate/schema.py:1445-1463,1899-1913`,
  `addons/angee/integrate/scheduler.py:20-32`
- **Change:** Add `Bridge.run_sync(*, now) -> SyncOutcome` wrapping mark-started /
  `self.sync()` / record-success-or-error; the three call sites call it and tally
  the returned outcome. **Caveat:** `sync_vcs_bridge` returns early on error while
  the others continue and tally — each caller keeps a small post-call branch, so
  callers do not collapse to one line.
- **Guardrail:** Existing scheduler + sync resolver tests stay green; add one
  asserting an exception in `sync()` records a sync error via `run_sync`.

### 2.3 `WebhookSubscription.deliver_test()` owns deliver-and-classify — medium, -3
- **Owner:** `WebhookSubscription`/`WebhookSubscriptionManager`
  (`addons/angee/integrate/models.py:1695,1751`) already owns body-encode + the
  `_failure_status`/`_error_message` classifiers via `deliver_event`.
- **Files:** `addons/angee/integrate/schema.py:1485-1503`,
  `addons/angee/integrate/models.py:1698-1748,1807-1847`
- **Change:** Add `deliver_test()` reusing that path; reduce the resolver to a
  dispatch. **Win is correctness, not LOC:** the resolver currently re-encodes the
  body byte-for-byte and hardcodes `status=""`, losing the real HTTP status that
  `_failure_status(exc)` extracts.
- **Guardrail:** Test that a failed test-delivery records the HTTP status, not `""`.

### 2.4 `ExternalAccount`/`Credential` projection properties — medium, -8
- **Owner:** `ExternalAccount` (`models.py:593`, already has `credential_status`)
  and `Credential` (`models.py:861`).
- **Files:** `addons/angee/integrate/schema.py:107-133,156-176`,
  `addons/angee/integrate/models.py:643-664,771-786`
- **Change:** Move `provider_slug`/`provider_label`/`provider_icon`/
  `provider_environment` to `ExternalAccount` properties and the legacy
  `display_name` fallback to `Credential.display_label`/`__str__`; resolvers shrink
  to `return self.<property>`. (The fallback diverges from
  `_oauth_credential_name`'s stored format — treat as a divergent legacy fallback,
  not exact duplication.)
- **Guardrail:** GraphQL field tests for the four provider projections + the
  unnamed-credential fallback label.

---

## Theme 3 — Base UI primitive thinning

Shared shapes in `@angee/base` re-implemented per call site, or strings/icons that
should read their owner.

### 3.1 FilterPicker disclosure editors share one card shell — medium, -18
- **Owner:** a new local `PickerEditorCard`/`PickerDisclosure` shell in DataToolbar.
- **Files:** `packages/base/src/toolbars/DataToolbar.tsx:411-508,605-699,701-762`
- **Change:** Extract the toggle + bordered card + footer Add button shell;
  `CustomFilterEditor`/`CustomGroupEditor` supply only their selects as children.
  **Scope caution:** the favorite form is a `<form onSubmit>` with one `Input` —
  structurally different; folding it in adds ceremony, so the clean win is the two
  div-based editors. -38 is inflated to ~-18.
- **Guardrail:** Storybook/interaction test that all editors still open/reset.

### 3.2 Filter combine/merge algebra onto the `Filter` class — medium, -38
- **Owner:** `Filter` (`packages/base/src/views/data-view-model.ts:117`), the
  declared owner of `DataViewFilter`.
- **Files:** `packages/base/src/views/GroupedList.tsx:962,973,995`,
  `packages/base/src/views/data-view-surface.ts:658`,
  `packages/base/src/views/DataPage.tsx:1006`
- **Change:** Add `Filter.merge(view)` (shallow) and `Filter.and(other)` (deep
  AND-fold); delete the byte-identical `mergeFilters` (two copies) and the
  `combineFilters`/`combineFilterRecords`/`filterRecord` trio, routing all sites
  through the methods. **Caveat:** the call sites use `ListFilter` (SDK) while
  `Filter` wraps base `DataViewFilter` — structurally compatible, needs a thin type
  bridge. `stableSerialize` stays a free helper.
- **Guardrail:** Unit test for deep AND-fold parity with the current GroupedList
  bucket-echo behavior.

### 3.3 Dedupe-by-key loops → one `dedupeBy(items, keyOf)` util — medium, -16
- **Owner:** one tiny `dedupeBy` util in `packages/base/src/lib` (no lodash/remeda
  in `docs/stack.md`; matches existing `titleCase`-style utils).
- **Files:** `packages/base/src/views/list-view-utils.ts:36,341`,
  `packages/base/src/views/ListInternals.tsx:1041`,
  `packages/base/src/views/data-view-model.ts:355` (actual ~400)
- **Change:** Add `dedupeBy<T>(items, keyOf)` and route `mergeById`,
  `buildGroupOptions`'s accumulation, `groupMeasuresFromColumns`, and
  `normaliseGroupStack` through it. **Caveat:** `normaliseGroupStack` interleaves a
  per-item transform that won't fully collapse into a pure `keyOf`.
- **Guardrail:** Unit test for first-write-wins-by-key on each consumer.

### 3.4 GroupedList user-facing strings → i18n — high, +8
- **Owner:** `useBaseT()` + `enBaseMessages` (i18next, `docs/stack.md`), already
  imported in this file.
- **Files:** `packages/base/src/views/GroupedList.tsx:363,413,535,648,799,908,957`
- **Change:** Replace ~8 hardcoded English literals (screen-reader-only) with
  `t('list.*')` keys, reusing `list.total`/`list.totalMeasure` and adding
  `noSubGroups`/`allRecords`/`noRecordsInGroup`/`itemsUnavailable` + pager-unit
  keys, matching `FlatListBody`. (Note: `"All records"` also lives in
  `BoardView.tsx:230` and `many2many.tsx:41` — broader gap to sweep later.)
- **Guardrail:** i18n key-coverage test / lint that flags raw JSX literals in the
  grouped body.

### 3.5 `groupFieldLabel` in DataToolbar → import the owner — high, -4
- **Owner:** `ListInternals.groupFieldLabel` (already the exported owner).
- **Files:** `packages/base/src/toolbars/DataToolbar.tsx:940-943`,
  `packages/base/src/views/ListInternals.tsx:1225-1228`,
  `packages/base/src/views/list-view-utils.ts:22`
- **Change:** Delete the byte-identical private copy; import from
  `../views/ListInternals` (as `list-view-utils.ts` already does). No circular dep.
- **Guardrail:** none beyond existing render tests (pure move).

### 3.6 Duplicate pageSize-sync effect → `useSyncPageSize` — high, -9
- **Owner:** a small shared hook in `data-view-surface.ts`.
- **Files:** `packages/base/src/views/data-view-surface.ts:144,240`
- **Change:** Extract the byte-identical 12-line ref+effect into
  `useSyncPageSize(dataView, pageSize)`; both surface hooks call it. (Confirms the
  prior plan's "only difference is data source" claim, which this preamble
  contradicts.)
- **Guardrail:** existing surface-hook tests stay green.

### 3.7 File glyph → catalogued `mimeType.iconKey` — medium, -7
- **Owner:** the MimeType catalogue (`icon_key`/`category`), exposed on
  `MimeTypeType` and already queried.
- **Files:** `addons/angee/storage/web/src/lib/file-display.ts:14-16`,
  `addons/angee/storage/web/src/views/file-columns.tsx:26,65,73`,
  `addons/angee/storage/web/src/data/documents.ts:153-157`
- **Change:** Project `mimeType{category,iconKey}` into the file row and have the
  glyph read `row.mimeType.iconKey` (fallback to one `file` glyph), deleting
  `fileIconName`'s mime-string sniffing. **Keep** `isImageMime` at
  `file-columns.tsx:65` — it gates inline-`<img>` renderability, not icon source.
- **Guardrail:** Column snapshot showing audio/video/archive rows get distinct
  catalogued glyphs.

---

## Theme 4 — Larger view decomposition

Riskier, higher-comment headless logic buried inside render components.

### 4.1 Extract `useRecordForm` headless hook from FormView — medium, ~0 net
- **Owner:** `@angee/sdk` (owns `useResourceRecord`/`useResourceMutation`,
  re-exports form bindings).
- **Files:** `packages/base/src/views/FormView.tsx:368-576,430-526`,
  `packages/sdk/src/resource-hooks.ts:248-377`
- **Change:** Move the seed/baseline/save refs+effects, `onSubmit`,
  `applyPatch`/`patchRecord`/`reload`, and the pure value mappers into a
  `useRecordForm(model, id, { fields })` hook. **Value is testability + reuse, not
  deletion:** the race-guard logic becomes unit-testable without DOM and is reused
  by RelationPicker's inline create. **Caveats:** `useForm` comes from
  `@tanstack/react-form` (not the SDK), and the mappers operate over `FieldDescriptor`
  (a base type) — a clean SDK move may invert the headless/rendered layering, so the
  hook might belong in `@angee/base`. Escalate the layer choice.
- **Guardrail:** Port the existing race/stale-closure cases to a DOM-free hook test
  before deleting them from FormView.

### 4.2 `afterFieldChange` pipeline → TanStack Form `listeners` — low, -10
- **Owner:** TanStack Form (`docs/stack.md`); `listeners.onChange`/
  `onChangeListenTo` own cross-field side effects (confirmed in installed v1.32).
- **Files:** `packages/base/src/views/FormView.tsx:617-675,627-631,758-762,878-882,908-911`
- **Change:** Declare the prefill + slug-derive reactions as field/form
  `listeners` so they fire on any value change; drop the four manual
  `afterFieldChange` calls. **The win is "controls can't silently skip the pipeline
  by construction," not LOC.** Caveat: slug-derive calls `setFieldValue` inside the
  reaction (recursion risk currently guarded by `manualSlugFieldsRef`); descriptor-
  driven dynamic fields make per-field wiring non-trivial — low confidence.
- **Guardrail:** Test that editing the title via the header input still derives the
  slug (the regression the routing comment documents).

---

## Theme 5 — Composer

Vendor knowledge and dead re-emission in `angee.compose`, which should own only
the composition seam.

### 5.1 Drop dead `db_table` + redundant `swappable` re-emit — high, -22
- **Owner:** the abstract source model's own `Meta`, which the emitted
  `class Meta(_<Name>Meta)` already inherits (Django `ModelBase.__new__` keeps
  DEFAULT_NAMES options).
- **Files:** `angee/compose/runtime.py:321-326,696-713`
- **Change:** Delete `_db_table_source` (fires for ZERO source models anywhere) and
  `_swappable_source` (inherited), relying on Meta inheritance. Keep
  `_rebac_meta_source` (non-DEFAULT_NAMES attrs are popped off Meta). Replace the
  literal-string assertion in `tests/test_compose.py:117` with one that verifies
  Meta inherits from `getattr(Abstract, 'Meta', object)` (a separate live-registry
  test can assert `_meta.swappable == 'AUTH_USER_MODEL'`).
- **Guardrail:** the new Meta-inheritance assertion; emitted runtime still migrates.

### 5.2 Move simple_history binding to `HistoryMixin` (asymmetry with RevisionMixin) — medium, ~0
- **Owner:** `angee.base.mixins.HistoryMixin` should own its simple_history
  binding the way `RevisionMixin.angee_model_decorators` owns reversion's;
  `django-simple-history` is the concern owner (`docs/stack.md`).
- **Files:** `angee/compose/runtime.py:277-278,455-468,715-739`,
  `angee/base/mixins.py:141-147,150-160`
- **Change:** Add a generic declarative "model field contribution" mechanism on the
  mixin (a sibling to `angee_model_decorators`) naming the import
  (`simple_history.models.HistoricalRecords`), field name (`history`), and a
  callable computing kwargs (app + excluded_fields). `_models_source` iterates it
  like `_model_decorators`; delete the three `HistoryMixin`-specific branches.
  **LOC ~0** (the excluded-fields logic relocates, not vanishes) — the win is
  vendor knowledge leaving the composer. **Note:** cannot reuse `ModelDecorator`
  (class decorator vs class-body field assignment); needs a new sibling mechanism.
- **Guardrail:** emitted `runtime/*/models.py` still declares
  `history = HistoricalRecords(...)` with identical excluded fields.

---

## Theme 6 — Resources / import-export

### 6.1 `LoadResult.from_rows` → import-export `Result.totals` — high, -18
- **Owner:** `import_export.results.Result.totals` (`docs/stack.md`: owns "row
  cleaning and row results"); it already accumulates created/updated/skipped via
  `increment_row_result_total`.
- **Files:** `addons/angee/resources/entries.py:606-644`,
  `addons/angee/resources/managers.py:99-113`, `addons/angee/resources/loader.py:552-561`
- **Change:** Drop the per-row import-type switch in `from_rows`; sum
  `result.totals[RowResult.IMPORT_TYPE_*]` across groups in `_import_groups`.
  `LoadResult` keeps three named ints for the command output line.
- **Guardrail:** Loader test asserting created/updated/skipped counts match before
  and after, including custom skip rows from `AngeeResource.import_row`.

### 6.2 `EntryGraph` topo sort → stdlib `graphlib.TopologicalSorter` — low, -10
- **Owner:** `graphlib.TopologicalSorter` (Python stdlib, py3.14).
- **Files:** `addons/angee/resources/entries.py:402-490`
- **Change:** Translate `CycleError` → `ResourceLoadError('cycle detected ...')`;
  delete the explicit indegree dict and the `len()`-based cycle check. **Caveat:**
  `static_order()` does NOT preserve position-stable ordering of independent nodes
  (the method's documented contract). Preserving it requires driving
  `prepare()`/`get_ready()`/`done()` and re-sorting the ready set by position each
  round — so most of the loop stays and only the cycle-detection half is a clean
  win. -10, low confidence.
- **Guardrail:** `test_entry_graph_respects_same_and_cross_addon_dependencies` plus
  a new same-position independent-node ordering case.

### 6.3 Delete dead `FROZEN_TIERS` constant — high, -2
- **Owner:** the file itself; the real freeze owner is `AngeeResource._skip_decision`
  (content-hash skip), which is tier-agnostic.
- **Files:** `addons/angee/resources/entries.py:81-82`
- **Change:** Delete the unreferenced constant + its misleading docstring (zero
  readers repo-wide; INSTALL/DEMO rows DO update on hash change, contradicting the
  docstring). If a tier freeze is ever wanted, it belongs in `_skip_decision`.
- **Guardrail:** none (grep-verified zero readers).

---

## Theme 7 — GraphQL runtime

### 7.1 Unify the gated-field exposure guard (revisions + aggregates) — medium, -8
- **Owner:** `angee.graphql.access` (the REBAC read-gating module, already imports
  `rebac.field_visibility.gated_read_fields`, owns `ChangeReadGate`).
- **Files:** `angee/graphql/revisions.py:128-136`, `angee/graphql/aggregates.py:65-70`,
  `angee/graphql/access.py:1-31`
- **Change:** Add `assert_no_gated_fields(model, field_names, *, surface)` raising
  the stable `ImproperlyConfigured`; replace both call sites, unifying the two
  drifting messages. Needs a `surface`/`kind` label param for the differing wording.
- **Guardrail:** Schema-build tests that exposing a gated field in a
  `revisioned_fields` set AND a `group_by` axis both raise.

---

## Theme 8 — Storage HTTP caching

### 8.1 Content-addressed download sets ETag/Cache-Control + conditional GET — medium, +6
- **Owner:** Django `FileResponse` ETag/conditional-GET +
  `django.utils.cache.patch_cache_control`, seeded from the existing `content_hash`.
- **Files:** `addons/angee/storage/views.py:51-75`,
  `addons/angee/storage/web/src/views/FileDetail.tsx:53-54`,
  `addons/angee/storage/web/src/views/file-columns.tsx:65`
- **Change:** Set `ETag = row.content_hash` and `Cache-Control: private, immutable,
  max-age=<token TTL>` on the `FileResponse` so same-URL re-fetches (img re-mount,
  `If-None-Match`) get 304 instead of a full re-stream. **Scope honestly:** the URL
  carries a TTL-bound signed token (max-age 900) minted per call, so cross-render
  URL cache reuse and CDN caching are limited; the real win is conditional-GET on
  the same token URL. +6 LOC; a library-leverage win, not deletion.
- **Guardrail:** View test: a second GET with `If-None-Match: <content_hash>`
  returns 304 with no body stream.

---

## Theme 9 — SDK metadata (build-time vs boot-time)

### 9.1 Move SDL→cache/field/subscription metadata to build-time codegen — medium, -60 (bundle/CPU)
- **Owner:** GraphQL Code Generator client-preset (`docs/stack.md`), already running
  per-project against `runtime/schemas/<schema>.graphql` after SDL emission.
- **Files:** `examples/notes-angee/web/src/main.tsx:18,61`,
  `packages/sdk/src/graphql-provider.tsx:21,27,29`,
  `packages/sdk/src/cache-config.ts:28`, `packages/sdk/src/model-metadata.tsx:197`,
  `packages/base/src/createApp.tsx:191`,
  `examples/notes-angee/runtime/schemas/console.graphql:1`
- **Change:** Emit `cacheConfigFromSchema`/`fieldMetadataFromSchema`/
  `changeSubscriptionFields` results as serialized data at codegen time (like
  `ResourceTypeMap`/action metadata); host imports that instead of raw SDL.
  `createSchemaRuntime` and friends consume pre-derived data, dropping `buildSchema`,
  the ~450-line SDL walk, and the 3481-line `?raw` SDL from the client bundle.
  **Honest LOC:** the walkers RELOCATE into a codegen plugin, not vanish (~0 net
  source LOC); the durable win is bundle size + per-boot CPU in every browser.
- **Guardrail:** A test/snapshot proving codegen-emitted metadata is byte-identical
  to the runtime-derived output for the example schemas.

### 9.2 Parse each boot SDL exactly once — medium, ~0..-5
- **Owner:** `graphql-provider.tsx` already builds the one authoritative
  `GraphQLSchema` (l27); `changeSubscriptionFields` re-parses the same string.
- **Files:** `packages/sdk/src/graphql-provider.tsx:27,39`,
  `packages/sdk/src/graphql-client.ts:154`, `packages/sdk/src/relay-invalidation.tsx:47`,
  `packages/base/src/createApp.tsx:191`
- **Change:** Derive the subscription field set from the already-parsed schema
  instead of `changeSubscriptionFields → buildSchema(sdl)`. **Scope narrowing:** the
  count is two parses, not "two-to-three" — the `graphql-client.ts:154` and
  `graphql-provider.tsx:39` branches are defensive fallbacks for external callers
  (public `createUrqlClient` export) and must NOT be removed. Honest net ~0..-5; the
  win is one fewer full type-graph build at boot for the 3481-line schema. Largely
  subsumed by 9.1 if that lands.
- **Guardrail:** assert the subscription-field set is unchanged after the rewire.

---

## Suggested next slices

Cheapest high-confidence deletions first; risk/scope rising down the list.

1. **6.3** Delete `FROZEN_TIERS` (-2, high) — grep-verified dead code, zero risk.
2. **3.5** Import `groupFieldLabel` owner in DataToolbar (-4, high) — pure move.
3. **3.6** Extract `useSyncPageSize` (-9, high) — byte-identical effect.
4. **1.5** Native SyncPage iteration (-8, high) — also fixes truncation bug.
5. **5.1** Drop dead `db_table`/redundant `swappable` re-emit (-22, high).
6. **6.1** `LoadResult.from_rows` → `Result.totals` (-18, high).
7. **1.1** `_session_backend` → `user.backend` (-14, high) — fixes brittle heuristic.
8. **1.4** Unify OAuth-client-by-slug + error codes (-8, high).
9. **3.4** GroupedList i18n sweep (+8, high) — fixes screen-reader-English gap.
10. **2.3** `WebhookSubscription.deliver_test()` (-3, medium) — correctness win.
11. **7.1** Unify gated-field guard (-8, medium).
12. **3.3** `dedupeBy` util (-16, medium).
13. **3.7** File glyph from catalogue (-7, medium).
14. **8.1** Download ETag/Cache-Control (+6, medium).
15. **1.3** `_json_value` → `model_dump` (-30, medium) — touches test doubles.
16. **3.1** FilterPicker shared card shell (-18, medium).
17. **3.2** Filter merge/and on `Filter` (-38, medium) — cross-package type bridge.
18. **2.4** ExternalAccount/Credential projection properties (-8, medium).
19. **2.2** `Bridge.run_sync` triad (-10, medium).
20. **5.2** simple_history binding → HistoryMixin (~0, medium) — ownership win.
21. **9.2** Single boot SDL parse (~0..-5, medium).
22. **1.2** REBAC `order_by` translation (-15, high) — needs upstream rebac change.
23. **6.2** `graphlib` topo sort (-10, low) — stability caveat.
24. **4.2** `afterFieldChange` → form listeners (-10, low) — recursion risk.
25. **4.1** `useRecordForm` extraction (~0, medium) — escalate layer choice first.
26. **2.1** Integration/VcsBridge mutations → `crud()` (-40..-55, medium) — biggest,
    needs careful update-path hook; do after the cheaper integrate slices land.
27. **9.1** SDL metadata → build-time codegen (bundle/CPU, medium) — largest lift;
    sequence after 9.2 and the typed-graphql codegen work.
