# Integrations: board + rich list, draft integrations, one-click connect

## Context

We want an integrations surface where you can **pre-create "draft" integrations of every kind**, see at a glance **what's connected / what needs attention**, **group by vendor capability**, and **connect with one click** — a board view (and rich grouped list) like the `Integrate / Overview` reference, but for the integrations themselves.

The current model blocks this, so the plan is **a model reseam first, then the views**:

1. **`Integration` and `Capability` are near-duplicate layers.** `Integration` (`addons/angee/integrate/models.py:1061`) is a *hub* that requires a working `credential` (PROTECT FK, line 1078) and carries a `capability_statuses` rollup (line 1090, `note_capability_status` line 1160). Capabilities (`Capability` abstract line 1203, `Bridge` line 1259, concrete `VCSIntegration` line 1381, `agents.InferenceProvider` `addons/angee/agents/models.py:102`) are *children* FK'd back to it. There is no "draft" state and no first-class "capability" to group on.
2. **The shared `BoardView` has no per-card action** (`packages/base/src/views/BoardView.tsx:229`; card = title + 3 columns wrapped in one navigating `<button>` at line 294), so one-click "Connect" needs an extension at its owner (`@angee/base`).

**Decision (agreed in planning):** collapse the seam so **`Integration` *is* the capability**, discriminated by an **`impl_class`** field; each kind that needs extra persisted fields links a **1:1 companion model** (a `Bridge`, an `InferenceProvider`, …) — the idiom `OidcClient` uses to refine `OAuthClient` (`addons/angee/iam_integrate_oidc/models.py:34`). The substrate (`OAuthClient`/`ExternalAccount`/`Credential`) stays shared; `Integration.credential` becomes nullable (a draft has none). This is mostly a **deletion** (the rollup) plus a flattening.

---

## Part A — Backend reseam (`integrate` base addon + `agents`)

### A1. `Integration` absorbs the capability concern
In `addons/angee/integrate/models.py`:
- Move `Capability`'s generic fields onto `Integration`: add `use_count_24h`, `error_count_24h`, `last_used_status` (it already has `status`, `config`, `last_used_at`, `last_error`, `last_error_at`).
- Move `Capability.report_status()` (line 1234) onto `Integration` as its **own** status write — no rollup; signature becomes `report_status(status: IntegrationStatus | str, error="")` and it just sets its own `status`/telemetry and saves.
- **Delete** `capability_statuses` (1090), `note_capability_status` (1160), `IntegrationStatus.from_capability` (1024), `IntegrationStatus.rollup` (1040), `Capability._capability_key` (1252).
- **Merge the status vocabularies.** `Integration.status` now carries the per-capability lifecycle, so `IntegrationStatus` (currently active/disabled/error, 1002) absorbs `CapabilityStatus`'s `PAUSED` (960) and adds `DRAFT`. Final set: **`DRAFT, ACTIVE, PAUSED, DISABLED, ERROR`**. Delete `CapabilityStatus`. StateField serializes `"ACTIVE"` on read / accepts `"active"` on write (`angee/base/fields.py`). "Needs attention" is the *view's* framing of `error` + expired/revoked credential — derived, not a stored state.
- Make **`credential` nullable** (`null=True, blank=True`); keep `account` nullable. Keep `vendor` (catalogue/branding) — vendor and impl stay independent (one vendor → several backends, per the `backend_class` docstring at 1401).
- Add **`impl_class`** = `ImplClassField(base_class=IntegrationImpl, registry_setting="ANGEE_INTEGRATION_IMPLS", default="none")`. `"none"` is a real registered null-object impl (category neutral, no companion) so the registry is non-empty at import and `crud()` create passes `full_clean` (the create input is `UNSET`/the default, never `None` — `docs/backend/guidelines.md` crud-create pitfall). Add `ANGEE_INTEGRATION_IMPLS` to `tests/settings.py` (the non-empty-registry obligation). This field is the board's "capability" axis and the create dropdown.
- Replace the unique constraint `(owner, vendor, credential)` (1109) with **`(owner, vendor, impl_class)`** — one integration per kind per vendor per owner; credential optional.

### A2. Concrete capabilities become **optional 1:1 companions**
- Rename `Capability` (abstract) to a companion base whose only job is the seam: change `integration` from `ForeignKey` (1215) to `OneToOneField("integrate.Integration", on_delete=models.CASCADE, related_name="%(app_label)s_%(class)s")`, and **strip** the fields/methods that moved up in A1. Keep `config` only if a companion needs companion-scoped config.
- `Bridge` (1259) stays the abstract **sync** companion (cursor/poll/subscription + `sync()`/webhook contract + `next_sync_at`). `record_sync`/`record_sync_error` (1296/1320) now call `self.integration.report_status(...)`.
- `VCSIntegration` (1381) → rename **`VcsBridge`** (a `Bridge` companion): keep `webhook_secret` + repo/discover helpers; **drop `backend_class`** (the backend resolves from `integration.impl`). `Repository.vcs_integration` FK (1556) retargets to `VcsBridge`.
- `agents.InferenceProvider` (`agents/models.py:102`) → a **non-sync companion** (extends the companion base, not `Bridge`): keep `name`, `base_url`; **drop `backend_class`**; `service_environment`/`credential` resolve via `integration`. `InferenceModel.provider` FK keeps its shape.
- A kind with no extra fields needs **no companion** — just `Integration(impl_class=…)`.

### A3. Unified impl registry + `IntegrationImpl` descriptor
- New common base **`IntegrationImpl`** (e.g. `addons/angee/integrate/impl.py`) declaring: `category: str` (board lane — `"vcs"`, `"inference"`, `"none"`, …), `companion_model: str | None` (dotted `"app.Model"` of the 1:1, e.g. `"integrate.VcsBridge"`), `label`/`icon`, and an `oauth_client` hint for connect. Kind behavior lives on subinterfaces: `VcsImpl(IntegrationImpl)` keeps the `VCSBackend` methods (`integrate/vcs/backend.py`), `InferenceImpl` keeps `list_models()` (`agents/backends.py`). The shared base is justified only by what it actually unifies (category + companion + connect metadata); keep it thin.
- **Instantiation stays on the owning row, no shape-inspection.** `Integration.impl` (property) resolves the descriptor via `ImplClassField.resolve_class` (`angee/base/fields.py:269`) and instantiates it bound to `(integration, companion)`, where `companion` is fetched from the descriptor's **declared** `companion_model` related-name (a direct, declared access — not a branch-on-kind guess). This *does* change inference's instantiation owner (today `InferenceProvider.backend` passes `self` the provider, `agents/models.py:154`; VCS passes `self.integration`, `models.py:1426`) — the impl now reads credential via `integration.credential` uniformly. Naming: the field is `impl_class`, the bound object is `Integration.impl`; reconcile the per-domain `.backend` accessors to the one chosen verb (`docs/guidelines.md:142`, one concept one name).
- One shared registry **`ANGEE_INTEGRATION_IMPLS`**, populated by **dotted-key autoconfig** — the established merge pattern (`integrate_github/autoconfig.py:9`, `agents/autoconfig.py:10`): integrate seeds `none` (+ `local`), `integrate_github` adds `github`, `agents` adds `anthropic`/`openai`/`manual`. The per-domain `ANGEE_VCS_BACKEND_CLASSES` / `ANGEE_INFERENCE_BACKEND_CLASSES` registries are removed (the `backend_class` fields are gone).
- `registry.py`: **delete `capability_models()`** (no readers once the rollup is gone); **keep `bridge_models()`** — it still discovers concrete `Bridge` *companions*; the scheduler (`scheduler.py:21`) filters `next_sync_at` and calls `bridge.sync()`; `bridge.integration` is now the 1:1 owner.

### A4. Connect / attach flow (shared credential)
- Keep the OAuth flow as-is (`connectAccountStart`/`connectAccountComplete`, `schema.py:539`/`585`; `connect.py:44`). Add a self-service mutation **`connectIntegration(integration_id)`** that:
  1. resolves the integration's `impl`/`vendor` → its `OAuthClient` (via the impl's `oauth_client` hint);
  2. looks up the **current user's** live `(user, oauth_client)` `Credential` through the existing `CredentialManager` (`models.py:665`), with the same ownership preflight `create_integration_from_credential` uses (`schema.py:1051`, `credential.user_id == request user`);
  3. if found → **attach it** and flip `draft → active` (no OAuth round-trip — the "share the client" path); else → return the authorize URL, and on completion attach the freshly minted credential.
- "Shared" = the same `oauth_client`'s credential serves all that user's drafts of that vendor; a vendor exposing several OAuth clients disambiguates by impl. This replaces `create_integration_from_credential` (which did the inverse: credential → new integration).

### A5. GraphQL, REBAC, webhooks, migration, tests
- **GraphQL** (`integrate/schema.py`, `agents/schema.py`): `IntegrationType` drops `capability_statuses`, gains `impl_class` + the nested companion 1:1 (`bridge`/`inferenceProvider`); reshape `VCSIntegrationInput/Patch`, `InferenceProviderInput/Patch` to companion shape; keep `changes(Integration, field="integrationChanged")` (1510); add the create path (B3) and `connectIntegration` (A4).
- **REBAC — keep the companion resource types.** `integrate/repository` field-derives from `relation vcs_integration: integrate/vcs_integration` (`permissions.zed:90`), `integrate/source`→`repository`, `agents/skill`→`source`, and `agents/inference_model`→`agents/inference_provider`. So **do not drop** `integrate/vcs_integration` / `agents/inference_provider`; retarget them to the renamed companions and keep them as thin definitions that field-derive from `integration` (they already do — dropping removes ~no duplication and would orphan the children). `rebac sync` after.
- **WebhookSubscription** (`models.py:1825`): confirm `impl_app_filter` / `integration_filter` / `matches` semantics survive — behavior now lives on `Integration` (app `integrate`) while a companion may live in `agents`, so the app a dispatch attributes to can shift. Add it to the blast-radius + tests.
- **Migration (data, not just schema).** `angee build` + `makemigrations integrate agents` emits schema ops, but a **data migration** must: turn each existing `VCSIntegration`/`InferenceProvider` row into a `OneToOne` companion of its `Integration`, copy its `backend_class` → `Integration.impl_class`, and **reconcile the new `(owner, vendor, impl_class)` uniqueness** (existing rows sharing a vendor with different credentials can now collide — pick a survivor or fold). For the example/dev DB a reset is acceptable; for any real data the backfill + collision pass is required before the new constraint applies. Never hand-edit `runtime/`.
- **Tests:** `tests/test_integrate.py`, `tests/test_integrate_scheduler.py` (rollup tests rewritten to assert a single `report_status` write, no rollup), `tests/test_integrate_graphql.py`, `tests/test_integrate_vcs.py`, `tests/test_agents.py`, `tests/test_agents_graphql.py`, and the `ConcreteBridge`/`SchedulerBridge` test fixtures.

---

## Part B — Frontend (`@angee/integrate`, `@angee/base`)

### B1. Extend the shared `BoardView` with a per-card action (owner-level)
- Add a **`cardActions?: (row) => ReactNode`** seam to `BoardView` (`packages/base/src/views/BoardView.tsx`), rendered in the card `<article>` **footer as a sibling of** `BoardCardShell`'s `<button>` (line 294) — never nested inside it (the button-in-button a11y trap). Thread the prop through the real path: `DataPage` → `ListView` board branch (`ListView.tsx:411`) → `BoardView` → `BoardRowCard`.
- The gallery card already has its own `renderCard` escape (`RowsListView.tsx:94`) — that's a **different seam**; reuse it for the list/gallery affordance rather than unifying the two. This is the "extend the primitive at its owner" move; do not hand-roll a board in the addon.

### B2. Integrations board + rich list (`addons/angee/integrate/web/src/views/`)
- Reshape `IntegrationsPage.tsx` to a `DataPage` over `integrate.Integration` with a **list ↔ board toggle** and group-by options: **`impl category` (default — the "vendor capabilities" axis), `vendor`, `status`** — all flat columns now, so the shared grouped `ListView`/`BoardView` do it directly (`docs/frontend/guidelines.md:61`).
- Status pill via the shared `STATUS_TONES` vocabulary (`widgets/status-tones.ts`): `draft`→neutral, `active`→success, `paused`→warning, `error`→danger; lane dots derive from the group column tone (`BoardView` `laneDotTone`, already present).
- VCS/inference become impl-categories of the one board; keep their detail/companion editing views.

### B3. Combined create form (impl dropdown drives the companion)
- Target UX (as requested): an `impl_class` **select** (`createOnly`, options via `useEnumOptions` lower-cased — the enum read/write pitfall, `docs/frontend/guidelines.md:148`) whose choice swaps in the companion's fields via `showWhen` (`Field.showWhen`, `FormView.tsx:1294`; hidden fields dropped by `mutationData`, 1322). `credential` is omitted → the row is a **draft**.
- Because the companion is a separate 1:1 model, the standard declarative `forms:` renderer (which submits to the generated `createIntegration`) can't write both rows. Two honest options:
  - **(primary)** a small **custom create control** (the guideline-gated escape hatch, `docs/frontend/guidelines.md:91`) submitting a **custom atomic mutation** that writes `Integration` + companion together (the established multi-row pattern, cf. `create_integration_from_credential`, `schema.py:1050`);
  - **(lighter alt)** standard `createIntegration` only, with the companion auto-created with defaults on save and its fields edited later on the detail view.
  Choose primary unless we decide the combined create isn't worth the escape hatch.

### B4. One-click connect on cards
- Add a **Connect `<Action>`** to each card via the B1 seam, `visibleWhen` the row is a draft/disconnected, calling **`connectIntegration`** (A4) — reusing the `ProvidersPage.tsx:64` connect-callback pattern (redirect or manual-paste, then `ctx.refresh()`). Connecting a vendor once lights up one-click connect for all its drafts (shared credential).

### B5. (Optional, agreed) Overview metric grid + seed
- Rebuild the `Integrate / Overview` metric cards with `DashboardView`/`MetricGrid` (`packages/base/src/fragments/MetricGrid.tsx`) backed by a small server stats resolver (vendors / oauth / enabled / oidc / accounts / credentials / needs-review).
- Ship a **resource seed** (an `install`-tier YAML beside `integrate/resources/master/010_integrate.vendor.yaml`) pre-creating **draft Integration rows** per known vendor×impl, so the board is populated out of the box.
- **Provenance hygiene:** the stats resolver name, seed YAML, comments, and commit messages carry **no** "prototype / p1 / reference / screenshot" provenance — name from the domain (`.agents/commands/lift.md` green-field rule).

---

## Phasing
1. **Model reseam (A1–A3, A5):** flatten Integration, merged status enum, `impl_class` + unified registry + null-object default, companions, delete rollup, keep+retarget companion REBAC types, data migration + constraint reconciliation; green backend tests + `schema --check`. *(Prerequisite; largest/riskiest.)*
2. **Connect/attach + catalogue (A4, B5 seed):** `connectIntegration` with credential resolution; draft seed.
3. **Views (B1–B4):** `BoardView` `cardActions` seam, board + rich list, combined create, one-click connect.
4. **Overview (B5 grid):** optional metric page.

## Verification
- Backend: `uv run examples/notes-angee/manage.py angee build` → `makemigrations integrate agents` (+ data migration) → `migrate` → `rebac sync` → `resources load` → `schema --check`; `uv run pytest tests/test_integrate*.py tests/test_agents*.py`.
- Frontend (affected packages): `pnpm run typecheck && pnpm run test && pnpm run build`; add new web classes to the app CSS `@source` and confirm the addon-composition test passes (frontend-guidelines pitfalls).
- End-to-end via `angee dev` from the workspace root: create a draft through the combined form → it shows on the board grouped by capability with a Connect button → click Connect → it flips to `active` and a second draft of the same vendor reuses the existing credential.
