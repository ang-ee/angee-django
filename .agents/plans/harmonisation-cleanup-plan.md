# Harmonisation & Cleanup Plan — backend + frontend

A repo-wide plan to (1) harmonise concepts expressed many ways, (2) DRY-optimise
duplicated shapes onto their owners, (3) find code that reinvents a locked
library, (4) flag empty/dysfunctional packages, and (5) finish or close
half-done refactors. The `display_name` harmonisation is the worked exemplar
that seeded this; it appears here as Phase 1.

## How this was produced

Five parallel investigations, all judged against the repo's own constitution
(`AGENTS.md`, `docs/guidelines.md`, `docs/backend|frontend/guidelines.md`,
`docs/stack.md`) — find-the-owner, DRY (incl. *similar-code-different-intent →
leave separate*), compose-don't-reimplement, one-way dependencies:

1. **Harmonisation workflow** — 12 finders across framework core + every base
   addon + every frontend package → 42 candidates → **adversarial verification**
   → **30 confirmed, 12 rejected** → synthesized backend/frontend plans + a
   completeness critic.
2. **Plans-record audit** — all ~47 `.agents/plans/` files, notes, handovers,
   and git history cross-checked against the code (unfinished work + naming).
3. **Backend library-reinvention finder** — parallel implementations of
   concerns a locked Python library already owns.
4. **Frontend library-reinvention finder** — same lens for urql/router/forms/
   data-grid/etc.
5. **Manual validation** of the `display_name` Node step (it builds clean).

**Status legend.** `[VERIFIED]` = adversarially verified against code (workflow
confirmed set, or the plans-audit code cross-check). `[SURVEY]` = an
observation not yet verified — confirm before acting. `[NEXT SCAN]` = a coverage
gap the critic flagged; not yet scanned.

---

## Phase 1 (exemplar) — uniform `display_name` / human label

One concept — "a record's human label" — is a stored `CharField` on some models,
a `display_label` `@property` on others, `get_full_name()` on the IAM `User`, and
bare `__str__` elsewhere; re-declared per GraphQL type; and **guessed** on the
frontend (`recordRepresentationFor`, `packages/resources/src/metadata.tsx:548`).

**Owner:** `AngeeNode.display_name` (GraphQL, `str(self)`) + per-model `__str__`
as the label source; backend-declared so the frontend stops guessing.

- **Step 0 — already applied (uncommitted):** `angee/graphql/node.py:24` now adds
  a `display_name` resolver to the `Node` interface (`str(self)`), overridable by
  a type's own field. Validated: `manage.py schema --check` passes; snake_case
  wire name `display_name`. *Commit this as Phase 1.* `[VERIFIED]`
- The full fold is specified by **Backend Theme 5** (integration label),
  **Backend Theme 10 / Frontend Theme A.1** (User `display_label`, *not* a
  `__str__` rewrite), **Frontend Theme A.2** (`created_by`/`*_label` projection),
  and the later narrowing of `recordRepresentationFor` once the backend declares
  the label uniformly.
- **Naming sweep:** no *separate* live `display_name`/`display_label` synonym
  pair survives beyond the integration family (Theme 5) and User (Theme 10);
  `display_label` is a real fallback property, not a dead synonym.

---

## Backend harmonisation & DRY  `[VERIFIED]`

Ten themes, ordered by leverage. Every schema-touching change must re-run
`manage.py schema --check` + frontend codegen.

| # | Theme | Owner / level | Effort · Risk |
|---|---|---|---|
| 1 | **Default queryset/aggregate wiring** — storage & knowledge re-declare the builder's own defaults via `get_queryset=`/`get_aggregate_queryset=` lambdas | `hasura_model_resource` defaults (core) | S · low |
| 2 | **`scoped_for_aggregate` probe re-inlined** instead of composing `aggregate_queryset` | `angee.graphql.data.aggregate_queryset` | S · low |
| 3 | **FK → public-id projection** hand-written ~9× (`strawberry.ID(public_id_for(...))`); knowledge invented `_as_id` | new `to_public_id`/`optional_public_id` in `angee/graphql/ids.py` | M · low |
| 4 | **Audit/user-ref projection** (`created_by`+`created_by_label`, + missing `updated_by`) copied across File/Page/Party; `principal_label` is the same | **IAM base addon** mixin (NOT core — core must not import IAM) | M · med |
| 5 | **Integration-family label** — 4 byte-identical `display_name→display_label` resolvers + 3 **dead** child `__str__` | small integrate GraphQL base over `AngeeNode` | S–M · med |
| 6 | **`actor_can_read` REBAC gate** inlined in resources/platform/operator | `angee/graphql/access.py` | S · low |
| 7 | **Model-field-path walker** duplicated (`hasura.py:637` ≈ `metadata.py:1112`) | `angee/graphql/introspection.py` (already owns sibling relation helpers) | S · low |
| 8 | **`oauth_client` connect-hint** read via defensive `getattr`; resolution duplicated | integrate-subtree bases + descriptor methods (NOT core `ImplBase`) | S+S · low |
| 9 | **Dead framework surface** — `is_public_data_model` (`angee/base/models.py:290`) has only test callers | delete; tests use `public_data_id_owner` | S · low |
| 10 | **User label diverges from the Node contract** — `get_full_name` + bespoke `full_name` field + standalone `user_display_label` all re-derive | `User.display_label` property (mirror the Integration idiom; do **not** rewrite `User.__str__`) | M · med |

Key sequencing: **1+2** first (largest deletion, near-zero risk) → **9+7+6**
(independent quick wins) → **3** (blocks **4**) → **4** → **10** (best before/with
4's IAM read) → **5** (dead-`__str__` part ships alone) → **8** last.

Full detail (files, line numbers, per-theme deletion analysis, and the
Option-A/reject-Option-B reasoning for Themes 5 & 10) lives in the synthesized
backend plan; the most load-bearing notes:

- **Theme 4/10 level rule:** `display_name` can live on `AngeeNode` because it's
  just `str(self)` with no addon dependency; the user-ref projection needs
  `user_public_id`/`user_display_label`, which are **IAM-owned**, so it must live
  in the IAM base addon, not core (`tests/test_layering.py` enforces this).
- **Theme 5 rejects** making `Integration.__str__` return `display_label`: it
  regresses to N+1 (the `only=["vendor","status"]` projection hint can't ride on
  `__str__`). Keep the resolver on a typed base.

---

## Frontend harmonisation & DRY  `[VERIFIED]`

Seven themes (A–G). Order: **B** (dead code) → **E**+**G** (naming/dup, low risk)
→ **F** → **D.1+C.3** (paired) → **A** (backbone) → **D.2** → **C.1+C.2** →
package/layering decisions last.

- **A — Uniform user/human label (frontend half of the exemplar).** A.1: `User`
  honours the Node `display_name` via `display_label` property + UserType resolver
  override (not `__str__`); collapse the bespoke `full_name` field. A.2: audit-user
  projection → one IAM-owned strawberry mixin; add `updated_by`/`updated_by_label`
  to match the frontend's already-uniform `NON_EDITABLE_FIELDS`
  (`packages/base/src/views/model-metadata-defaults.ts:34`). M · med. *Backbone
  — unblocks narrowing `recordRepresentationFor`.*
- **B — Dead code (do first).** B.1: dead `ListView` import in **23** page
  components; enable `noUnusedLocals` in `tsconfig.base.json` (also clears ~8 other
  dead locals it surfaces). B.2: dead `IamRoles` document
  (`iam/web/src/documents.ts:10`) — re-derive `IAMRole` from `IamOverview` (commit
  `d82bf9e9` did the sibling `IamGrants`). S · low.
- **C — Compose, don't re-implement.** C.1: `context-menu.tsx`/`dropdown-menu.tsx`
  are a near-duplicated styled-part set over Base UI's shared `Menu` parts →
  shared `menu-parts.ts` factory (~−350 lines; latent value — ContextMenu has no
  product consumer yet). C.2: settings shell + titled `Section` hand-rolled 3× →
  compose `SurfacePanel`/`DetailSection` (NOT `PageHeader` — wrong intent). C.3:
  hand-written `OAuthConnectPayload` interface mirrors SDL → type from the
  generated `TypedDocumentNode`.
- **D — GraphQL document/selection DRY.** D.1: `ConnectIntegrationResult`
  selection authored twice → one named fragment in `@angee/integrate` (drop the
  dead `integration { id status }` over-fetch). D.2: IAM grant-tuple row parsed &
  typed twice with camel/snake drift → expose `principal_ref`/`role_name`/
  `namespace` on `IAMGrantType` (reuse `_grant_rows()` helpers); delete the
  client parse. *The exemplar disease in the grant dimension.*
- **E — Naming drift / find-the-owner (single-package).** E.1: `relationLabel`
  re-implements field→label → use `groupLabel`. E.2: `UserMenu` `initials()` →
  `avatarInitials` owner. E.3: `ChromeMenuTone` re-types a `tones.ts` subset →
  `Extract<Tone,…>` + guard. E.4: `BaseMenuItem`/`ChromeMenuItem` duplicate 9
  chrome fields → one `ChromeMenuExtra` interface.
- **F — local-rows surface dup** — `useClientResourceViewSurface`/
  `useRowsResourceViewSurface` duplicate the page query + clamp → `useLocalRowsPage`
  hook (lowest-leverage; pass filter as a param, don't probe the object).
- **G — Operator quarantine dup** — `useStableVariables`/`useStableValue`
  duplicated verbatim; the pure helpers are safe to import from `@angee/refine`.

---

## Naming harmonisation

- **Code renames are fully complete — DO NOT re-open** `[VERIFIED]`: `connect`→
  `integrate`/`messaging`/`parties`; `Capability`→`Bridge`/`VcsBridge`; `Oidc*`→
  `OAuth*`; `provider/integration_not_connectable`→`oauth_client_not_connectable`;
  `DataPage`/`DataView`/`data_query`/`GroupListView`/`useResourceList`→
  `ResourceList`/`ListView`/`hasura_*`. No old/new synonym pair survives in live
  code (`GroupedList` is an unexported `ListView` internal — intended).
- **B1 — Plan-document vocabulary drift** `[VERIFIED]` (S · low): historical
  plans still narrate deleted APIs — `data-management-odoo-parity.md` (36 refs),
  `listview-groupview-board-split.md` (18), `connect-remediation.md` (3). Add a
  "superseded — vocabulary stale" banner or archive them; code is the source of
  truth and these mislead the next agent.
- **In-code naming, single-package** (folded into Frontend Theme E above):
  `relationLabel` / `initials` / `ChromeMenuTone` / menu-item field duplication —
  one concept, several spellings; each routes to its existing owner.
- **Doc rule to add** `[SURVEY]`: no doc owns "where `display_name` is declared."
  Add a terse rule to `docs/backend/guidelines.md` naming `AngeeNode.display_name`
  as the canonical label owner (per the exemplar) so new models don't re-fragment.

---

## Reinvented library-owned concerns (delegate to the owner)

The codebase is, on this axis, **remarkably clean** — both finders confirmed that
nearly every probe resolves to documented thin glue (urql quarantine, date-fns,
tailwind-merge, i18next, TanStack Virtual; sqids, pyjwt, django-import-export,
Django `Collector`/`signing`, SDK-owned retries). Two genuine items:

- **Backend — `revisions.py` reinvents strawberry-django's field-type map**
  `[VERIFIED]` (M): `_field_annotation` (`angee/graphql/revisions.py:191-213`) is
  an `isinstance` ladder duplicating `strawberry_django.fields.types.field_type_map`
  / `resolve_model_field_type`. The copy **drifts** — a `StateField`/
  `TextChoicesField` hits the `else` and is exposed as a bare `String`, silently
  dropping the enum in revision types. Fix: look up via the library's map; keep
  only the `null → Optional` wrap as glue. (Confirm the import path is stable on
  the pinned editable dep.) *This is the real, high-value half of the field-type
  ladder theme — see "Do NOT touch" for why the other three ladders stay.*
- **Frontend — no third-party reinvention; one Angee-primitive gap** `[VERIFIED]`
  (conf 0.6): IAM `SchemaPage.tsx:102-289` hand-rolls an accessible listbox
  (roving-tabindex, `role="listbox"`, arrow/Home/End). No base owner exists. If
  the pattern recurs, add a headless filterable-listbox to `@angee/base` over
  `@base-ui/react` and compose it (~−60 lines); one occurrence is borderline
  against the 3× threshold, so this is "extend base when it recurs," not urgent.

Out-of-scope but flagged: two derive-during-render effects (`OverviewPage.tsx:100`,
`SchemaPage.tsx:106`) → React review. The `except A, B:` form in ~9 modules is
**not a bug** (Python 3.14 parses it as a tuple catch) — a lint-style parens nit.

---

## Empty / stub packages & structural decisions

**Keep as-is (correctly structured)** `[VERIFIED]` — document them as exemplars,
don't touch: `integrate_github`, `parties_integrate_carddav`,
`agents_integrate_anthropic`/`openai` (backend-only adapter addons, no models by
design); `operator`/`platform` (table-less REBAC type-anchor models); the sparse
`messaging`/`parties`/`resources` web packages (they correctly compose shared
`@angee/base` primitives).

**Decide — empty/half-migrated frontend packages** (ownership decisions, not
mechanical fixes; resolve before they accrete more drift):

- **`@angee/app`, `@angee/ui` are empty stubs** (`packages/{app,ui}/src/index.ts`
  = `export {}`). Fill or delete per target architecture. **Blocker:** the
  frontend package-layering doc (current vs target; which package may import
  `compose`) isn't written — write the `docs/frontend/guidelines.md` layering
  section first, then act. This is the same item as plans-audit **A1**.
- **`@angee/data` (10 files) — half-done data-provider boundary.** Make it the
  exclusive owner of refine data hooks, or formalize `@angee/base` as the owner.
  Don't leave it mid-migration.
- **`@angee/sdk` vs `@angee/app` composition ownership** (`defineAddon`/
  `createApp`) — document the migration path or freeze the assignment.

**Consider** `[SURVEY]`: `agents_integrate_anthropic`/`openai` duplicate
autoconfig + resource manifests — extract the shared inference-backend
registration into `agents` (see Backend Theme 8 / survey).

---

## Unfinished / half-done planned work  `[VERIFIED]` (plans-record audit)

| # | Item | Evidence | Recommendation | Effort · Risk |
|---|---|---|---|---|
| A1 | **Refine package split half-done** | `packages/{ui,app}/src/index.ts` = `export {}`; old `@angee/base` (308 files), `@angee/data`, `@angee/sdk` live; example consumes old `@angee/base` (`examples/notes-angee/web/package.json:16`); Phases 5–6 unstarted | Finish Phases 3–6, **or** formally freeze (ledger itself calls the split "cosmetic") | L · high |
| A2 | **`local-rows.ts` forbidden evaluator survives** | `matchesLocalLookup` (`packages/base/src/views/local-rows.ts:174-217`) hand-rolls a `_bool_exp` engine; live consumers in storage + operator | Finish (drive via TanStack client row models, one filter codec) **and reconcile the doc** — `docs/frontend/guidelines.md:141` *endorses* it while the ledger says delete | M · med |
| A3 | **Parallel i18n path** | i18next provider mounted but **0 live `useTranslate`**; all components use hand-rolled `useNamespaceT`/`interpolateMessage`; `runtime.ts` spins a throwaway instance per call | Route all translation through one shared i18next instance via `useTranslate`; delete the parallel store | M · low |
| A4 | **Four parallel field-type `isinstance` classifiers** | `hasura.py:683,691`, `metadata.py:763,842`; group-key alias recomputed (`hasura.py:679,134`) | Finish at owner level (publish the alias contract upstream, then narrow) **or** formally close as architect-deferred — currently in limbo. *Coupled to the `revisions.py` library fix above; see "Do NOT touch" — do NOT fuse into one classifier* | M · med |
| A5 | **Downstream `@angee/gql` codegen never emitted** | alias hard-pinned to the example (`tsconfig.base.json:7`); `angee/compose/runtime.py:583` emits no alias; `templates/` has no `@angee/gql` | Emit the per-project alias + codegen config from the composer/`templates/`. Blocks any non-monorepo consumer | M · med |
| A6 | **Per-agent MCP tool authz unbuilt** | `addons/angee/agents/mcp_verifier.py:50,56` TODOs — agent borrows its credential-owner's full identity; no per-tool authz | Build under mcp-over-graphql B2 (distinct agent subject + `Agent.mcp_tools` allow-list) **or** surface as a documented authz limitation | M · **high (security)** |
| A7 | **`AngeeModel` public-id → raw-`pk` fallback** (aspirational) | `angee/base/models.py:188,191,201` | Backlog — plan says "eventually"; tighten only with the broader sqid-base migration | S / L · med |

**Closed / superseded (don't reopen)** `[VERIFIED]`: `connect-addon`/
`connect-remediation`, `agents-addon`, `rich-agent-chat`, `impl-class-field`,
`mcp-over-graphql` A+B1, `mcp-asgi-mount`, `handover-agent-chat`. **Not started /
design-only:** `platform-cluster-and-studio`, `openbao-agent-secrets`,
`dev-stack-disconnected` (future publish), `addon-deletion-research-checklist`.

**`[SURVEY]` — prior DRY-audit waves marked `[x]` but deletion of old code
possibly skipped — verify each before acting:** admin-delete copies
(`iam/schema.py:584`, `integrate/schema.py:618` vs `angee/graphql/crud.py:211`);
inline create/rename controls (`NewFolderControl`/`SelectedFolderControl`/
`NewPageControl`) vs a shared `@angee/base` primitive; status-tone maps
(`AgentChat.tsx:118`, `file-display.ts:21`) vs `packages/base/src/widgets/status-tones.ts`;
ExternalAccount/Credential projection properties duplicated in `integrate/schema.py:106-127`
vs model `@property` (`integrate/models.py:656-691`); `docs/composer.md` API-inventory
drift. Source: `dry-architecture-audit-fix-plan.md`, `post-dry-audit-cleanup-findings.md`.

---

## Dead code (quick wins, mechanical)

- `is_public_data_model` — test-only framework surface (Backend Theme 9). `[VERIFIED]`
- 23 dead `ListView` imports + ~8 dead locals; `IamRoles` document (Frontend Theme B). `[VERIFIED]`
- `vault_label` re-reads `Vault.name` inline (`knowledge/schema.py:104`) → one-line `str(vault)`. `[VERIFIED]`
- `ParsedAddressbook.sync_token` reserved-but-unused (`parties/backends.py:44`) — fix docstring or drop. `[SURVEY]`

---

## Do NOT touch (different intent / verified-not-an-issue)  `[VERIFIED]`

The verification pass rejected 12 candidates. The load-bearing rejections:

- **Do NOT fuse the four field-type `isinstance` ladders into one classifier.**
  Only `revisions.py:_field_annotation` is a real fix (use the library map,
  above). `_measure_ops_for_field` is a distinct aggregability branch; the Hasura
  dialect scalars (`hasura.py:683,691`) have **no library owner**
  (strawberry-django-hasura exposes no op list). A unified classifier would lose
  Decimal/Float/Time distinctions and re-implement stack-owned behavior.
- **`public_id_value` model method vs `angee/graphql/ids.py:21` free function** —
  a name collision, not a merge. If addressed, **rename** the free function
  (`public_id_text`); don't inline it (3 call sites; does real boundary coercion).
- **`public_id_of`/`public_id_for`/`public_data_id_*` cluster** — the "four
  functions probe `_meta`" premise is false; only one probes, the rest delegate.
  Deliberately ownerless build-time transforms over arbitrary `type[Model]`
  (can't be `SqidMixin` classmethods). Blessed by `docs/guidelines.md:158`.
- **Related-row `*_label` resolvers** — the user-row family has **already**
  converged on `user_display_label`; `provider_label`/`owner_label` are distinct
  intent. Routing through an exposed Node would defeat the REBAC `system_context`
  unexposed-read these exist to honour. (Only `vault_label` is a real one-liner —
  see Dead code.)
- **`__str__` `display_name or <fallback>`** on Vendor/InferenceModel — only two
  true matches (OAuthClient's is a *composed* fallback), below the 3× threshold,
  and a thin slice of the exemplar's canonical fix. Leave separate.
- **`relationLabel`→`PageHeader`** — rejected; `PageHeader` is page chrome, the
  settings sections are in-content headings → compose `SurfacePanel` instead.

---

## Coverage gaps & next scans  `[NEXT SCAN]`

Honest about what wasn't verified this pass:

- **`addons/angee/mcp/`** (incl. 18 KB `graphql.py`) — unscanned. Likely a **third**
  field-path walker (cf. Theme 7) and a hand-rolled auth gate (cf. Theme 6).
- **`platform/schema.py`** walks `model._meta.fields`/`many_to_many` (`:165,181,191`)
  — probably a third copy of the `metadata.py:1112` walker.
- **`iam_integrate_oidc/`** (~1078 LOC) — its `identity.py` is a sibling of the
  `user_display_label` owner; check for duplicated id→label / public-id helpers and
  the FK→public-id inline shape (Theme 3).
- **`packages/storybook`** (86 files) — largest unscanned TS region; spot-check for
  stories re-implementing `@angee/base/src/ui` primitives.
- **`packages/refine`/`data`/`sdk`** — zero findings; the "data ownership split"
  claim (A1/empty-packages) is assertion-only — diff their `useList`/`useCreate`
  hooks against `@angee/base` consumers for concrete evidence.
- **Display-label siblings** — extend Theme 4/A.2 to `owner_label`
  (`knowledge/schema.py:49`), `principal_label` (`iam/schema.py:202,975`),
  `agents/schema.py:440`, and the FE `recordRepresentationFor`
  (`metadata.tsx:548`) — same `user_display_label` owner.
- **Phantom — do NOT chase:** the survey's "audit integrate for
  `CharField(choices=...)`" is false; integrate uses `StateField` (zero
  `CharField(choices=)` across addons).

---

## Recommended global sequencing

1. **Commit Phase 1** (the `node.py` `display_name` resolver). Quick mechanical
   dead-code wins: Backend Theme 9 + Frontend Theme B + `vault_label`.
2. **Backend Themes 1+2** (largest deletion, near-zero risk), then **7+6**;
   **`revisions.py` library fix** (correctness — fixes the enum drop).
3. **Frontend Themes E+G+F**, then **D.1+C.3**.
4. **The exemplar backbone:** Backend Theme 3 → 4; Backend Theme 10 / Frontend
   Theme A; then **narrow `recordRepresentationFor`**. Backend Theme 5 (+ dead
   `__str__`). Frontend D.2 pairs with the IAM work.
5. **Frontend C.1+C.2**; Backend Theme 8.
6. **Decisions/escalations:** A1 package split & frontend layering doc; A4
   field-classifier resolution (or formal defer); A5 downstream codegen; **A6 MCP
   authz (security — prioritise the decision)**; B1 plan-doc banners.
7. **Verify the `[SURVEY]` prior-wave deletions**; run the `[NEXT SCAN]` targets.

**Process notes (constitution):** Themes 4/5 introduce strawberry-django
multi-base / type-to-type inheritance that exists nowhere else — confirm the
pattern with the architect once, then reuse. Every DRY/refactor change must
report net shape (what was deleted, which callers got thinner). Capture durable
rules in `docs/` (the `display_name` owner rule; the type-anchor and
backend-only-adapter exemplars), not in private memory.
