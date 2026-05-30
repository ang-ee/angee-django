# Base-Framework Feature Parity Matrix — P0 · P1 · Current

Detailed parity matrix of the **base framework modules only** (framework core +
base addons; consumer/product addons like `agents`, `knowledge`, `operator`,
`notes` are out of scope) across three sibling checkouts.

- **Date:** 2026-05-30
- **Repos compared:**
  - **P0** — `/Users/alexis/Work/fyltr/angee-django-p0` (earliest; "blocks" era,
    `blocks/angee/angee/`, "assets")
  - **P1** — `/Users/alexis/Work/fyltr/angee-django-p1` (full predecessor;
    `src/angee/<addon>/`, mid GraphQL rebuild)
  - **Current** — `/Users/alexis/Work/fyltr/angee-django` on branch
    `wip-base-lift-refactor` (mid-refactor "base lift"; only `src/angee/base/`
    exists so far)
- **Evolution direction:** P0 → P1 → Current.
- **Legend:** ✅ full · 🟡 partial / present-but-different · ❌ absent · — n/a

> The CLI (`angee dev`, `init`, `ws`) is the same external Go/Cobra binary
> (`github.com/fyltr/angee`) in all three — it is not Python and not in any of
> these repos. See the CLI section.

---

## 1. Repo identities & shape

| | P0 | P1 | Current |
|---|---|---|---|
| Backend layout | `blocks/angee/angee/` (+ `blocks/auth`, `auth-oidc`, `storage`, …) | `src/angee/<addon>/` (core, compose, graphql, resources, auth, integrate, integrate_auth_oauth, storage, testing) | `src/angee/base/` **only** |
| Discovery unit | "block" (packaging entry-point `angee.blocks` + `[tool.angee]`) | "addon" = Django `AppConfig` in `INSTALLED_APPS` | "addon" = Django `AppConfig` in `INSTALLED_APPS` |
| Source-model metaclass | `AngeeModelMeta` (role/target tagging) | `AngeeModelMeta` (lifts ~40-key `AngeeMetaConfig` off `Meta`) | **none** — plain abstract `AngeeModel` + classmethods |
| Tabular data term | "assets" | half-renamed `resources`/`Resource` but AppConfig attr/CLI still `assets` | fully "resources" |
| Frontend | monolithic `@angee/sdk` block (`blocks/angee/ui/react`) + stale `react-angee` dist | split `@angee/sdk` (headless) + `@angee/base` (rendered) in `packages/` | **none lifted** |
| GraphQL strategy | codegen → `runtime/<app>/schema/*.py` | codegen → `runtime/<app>/graphql.py` (mid-rebuild) | runtime merge of authored Strawberry parts; SDL-only emit |

**One-sentence character of each:** P0 is a feature-rich but heavy
codegen/metaclass machine with its own block system; P1 is the matured, broadest
implementation (the lift source of truth); Current is a deliberate strip-down to
the smallest base seam — backend-only, no metaclass, GraphQL/admin/aggregates
codegen shed, frontend/auth/storage not yet lifted.

---

## 2. Executive summary matrix

Subsystem-level maturity, plus what the Current branch has lifted so far.

| Subsystem | P0 | P1 | Current | Current lift status |
|---|---|---|---|---|
| **Composer / composition pipeline** | ✅ (13-phase, codegen-heavy) | ✅ (4-step, codegen) | 🟡 (lean: models+SDL+migrations+permissions) | **Lifted & simplified** — core seam present |
| **Resources / Assets** | ✅ (import-export engine) | 🟡 (regressed: hand-rolled loader) | ✅ (import-export restored + URL fetch) | **Lifted, best-of-three** |
| **GraphQL** | ✅ codegen | ✅ richest (rebuild) | 🟡 merge+crud+changes+named-schemas only | **Partially lifted** — codegen/aggregates/scalar missing |
| **REBAC / authorization** | 🟡 (blueprint + skeleton) | ✅ (full) | 🟡 (compose-side glue only) | **Glue lifted** — base addons (.zed, roles, GQL enforce) pending |
| **Model layer / mixins / fields** | ✅ (sqid+audit, no state/encrypt) | ✅ (peak: state+encrypt+revisions) | 🟡 (Timestamp+Rebac+sqid-lookup stub) | **Minimal** — most mixins/fields not lifted |
| **Auth / Identity / OIDC** | ✅ (2 blocks, requests+pyjwt) | ✅ (auth + integrate + integrate_auth_oauth) | ❌ | **Not lifted** (no User model, no pyjwt/httpx dep) |
| **Storage** | 🟡 (models + UI mockup, no upload) | ✅ (full presign/MIME/REST+GQL/GC) | ❌ | **Not lifted** (stack.md intent only) |
| **Frontend (SDK + base binding)** | 🟡 (monolith, runtime registration) | ✅ (sdk/base split, build-time compose) | ❌ | **Not lifted** (zero .ts/.tsx) |
| **CLI / dev supervisor / transport** | ✅ (Go CLI + runserver) | ✅ (Go CLI + daphne + storybook) | ✅ (Go CLI + daphne, per-schema routes) | **Lifted** — settings/asgi/urls in `base`; trimmed templates |
| **Testing & quality tooling** | ✅ (synthetic-project + vitest + 2-job CI) | 🟡 (scenario addon; node --test; broken CI) | 🟡 (pytest only; no CI/JS/synthetic fixture) | **Backend essentials only** |

---

## 3. Detailed subsystem matrices

### Composer / Composition Pipeline

**Approach & evolution.** P0 (`blocks/angee/angee/builder/`) is a 13-phase build
orchestrator driven off Python packaging entry points + per-block `[tool.angee]`,
with a custom `AngeeModelMeta` consulting a global descriptor registry, emitting a
wide runtime tree (apps/models/schema/admin/aggregates/operations/zed) plus a full
migration sub-system. P1 (`src/angee/compose/` + `core/` + `graphql/`) keeps the
metaclass model layer but moves discovery onto Django's app registry
(`AngeeAppConfig` in `INSTALLED_APPS`); the pipeline collapses to
`discover → validate assets → emit_runtime → emit_graphql_runtime`. Current
(`src/angee/base/compose/`) deletes the metaclass (plain abstract `AngeeModel`),
emits only `models.py` + `__init__.py` per app, and builds GraphQL at runtime from
authored Strawberry parts (only SDL persisted).

| Capability | P0 | P1 | Current |
|---|---|---|---|
| Addon discovery source | 🟡 entry points + `[tool.angee]` (`builder/discovery.py`) | ✅ Django app registry `AngeeAppConfig` (`compose/discovery.py:discover_addons`) | ✅ Django app registry `BaseAddonConfig` (`discovery.py:discover_addons`) |
| AppConfig contract | 🟡 metadata in `pyproject.toml` → `BlockDescriptor` | ✅ `AngeeAppConfig`: namespace/kind/depends_on/assets/sqid_prefix/compose_emits_runtime/graphql_module (`core/apps.py`) | ✅ `BaseAddonConfig`: depends_on/rebac_schema/resources/get_model_classes/get_schema_parts (`apps.py`) — leaner |
| Deterministic ordering | ✅ Kahn toposort, alpha tie-break (`builder/dag.py`) | ✅ DFS toposort over depends_on (`discovery._toposort`) | ✅ DFS toposort + alias/cycle/dup detection (`discovery._toposort`) |
| Fail-fast collisions | ✅ double-owner/orphan/cycle | 🟡 dup-name + GraphQL field collisions | ✅ field collisions (`emission._check_field_collisions`) + GraphQL root-field + unknown-extension-target |
| Source-model contract | 🟡 `AngeeModelMeta` + registry (`models/meta.py`) | 🟡 `AngeeModelMeta` lifts `AngeeMetaConfig` (`core/models.py`) | ✅ plain abstract `AngeeModel` (Timestamp+Rebac), **no metaclass** (`mixins/models.py`) |
| `extends` symbolic ref | 🟡 implicit via MRO ancestry | ✅ `class Meta: extends = "app.Model"` | ✅ `extends = "app.Model"` plain class attr (`get_extension_target`) |
| Cross-addon extension compose | ✅ group-by-target, ordered MRO | ✅ `_contributor_bases`, abstract-or-concrete target | ✅ `_extensions_for` grouped+sorted, validates targets (`emission.py`) |
| Generated `apps.py` per app | ✅ | ✅ `_runtime_app_source` | ❌ reuses source AppConfig + `MIGRATION_MODULES` redirect |
| Generated admin | ✅ | ✅ honors source override | ❌ |
| Abstract→concrete emission | ✅ (`builder/emitter.py`) | ✅ (`compose/model_emission.py`) | ✅ (`compose/emission.py`) |
| GraphQL build-time codegen | ✅ per-app schema/types/mutations/aggregates | ✅ per-app `graphql.py` + merged schema (`graphql/emit.py`) | ❌ **none** — runtime merge instead |
| GraphQL runtime schema build | ❌ | 🟡 imports generated `schema.py` | ✅ `merge_types` of authored parts (`graphql/schema.build_schema`) |
| SDL persisted for drift | ✅ `.angee-schema.graphql` | ✅ `schema.graphql` | ✅ per-name `runtime/schemas/<name>.graphql` + drift check |
| makemigrations | ✅ in-proc per-app | 🟡 subprocess | ✅ in-proc `call_command` then optional migrate (`pipeline.run`) |
| Migration header determinism | ✅ | ✅ | ✅ (`emission._normalize_migration_headers`) |
| Pre/data-migration phases + state | ✅ `.angee-migrations.json` | ❌ | ❌ |
| Revisions/history emission | 🟡 simple-history line | ✅ `<Model>Revision` sibling table | ❌ |
| `--check` drift detection | ✅ tempdir byte-diff | ✅ tempdir dircmp | ✅ tempdir per-file cmp + SDL check (`pipeline.run(check=True)`) |
| Two-build determinism harness | ❌ | ✅ `_determinism_check.py` | ❌ |
| Build manifest | ✅ `.angee-build.json` (+hash) | ✅ `.angee-manifest.json` (+input hash) | 🟡 `.angee-manifest.json` (**no input hash**) |
| Permissions/Zed composition | ✅ per-app + unified | ✅ concatenated `.angee-permissions.zed` | ✅ concatenated `runtime/permissions.zed` + `rebac sync` |
| Watch mode | ✅ | ✅ | ❌ (only build/clean) |
| Host shape | 🟡 consumer `host/` + many runtime apps | ✅ `compose_defaults` mutates settings dict | ✅ `compose_defaults` **returns** dict; `src/host/` + `src/example/notes` |

**Evolution highlights:** discovery moved from packaging → Django registry (P1
onward); the custom metaclass is being **deleted** in Current (behavior lifted to
classmethods on `AngeeModel`); `extends` API changed shape three times; GraphQL
codegen + admin + aggregates + revisions + watch + determinism-harness all
**dropped** from Current so far; pipeline stages collapsed 13 → 4 → lean. Current
is clearly lifting P1's tempdir `--check`, manifest convention, `compose_defaults`,
header normalization, and path-deduped Zed concat.

---

### Resources / Assets

**Approach & evolution.** P0 (`blocks/angee/angee/assets/`) is the feature-complete
early implementation — a manifest→loader design on `[tool.angee.assets]` TOML,
binding **django-import-export + tablib** (custom resource, xref widgets, frozen
buckets, dry-run, GenericFK `Assets` ledger). P1 (`src/angee/resources/`) is a
**regression** here: package renamed but the loader was rewritten by hand
(csv/yaml + manual coercion), dropping import-export/tablib/ordering/dry-run/xref
checks while adding rebac-relationship loading. Current
(`src/angee/base/resources/`) is the **cleanest synthesis** — restores the
import-export engine, splits into focused modules, moves ops onto a
`ResourceQuerySet`/`ResourceManager`, adds URL fetch + an explicit `Resource.Tier`
enum, and completes the `assets`→`resources` rename.

| Capability | P0 | P1 | Current |
|---|---|---|---|
| Tier/bucket model | ✅ `AssetBucket` choices | 🟡 plain CharField tier (no choices) | ✅ `Resource.Tier` TextChoices + `from_value()` |
| Where addons declare files | 🟡 `[tool.angee.assets]` TOML | ✅ `AppConfig.assets` dict | ✅ `AppConfig.resources` ClassVar (enum/str keys, cached normalize) |
| Idempotent import (hash skip) | ✅ | ✅ | ✅ (`loader.import_row`) |
| django-import-export | ✅ `_AngeeResource`/`modelresource_factory` | ❌ hand-rolled `_upsert_object` | ✅ `AngeeResource`/`resource_for` |
| tablib parsing | ✅ | ❌ raw csv/yaml | ✅ csv/tsv/xls/xlsx/ods + native yaml/json |
| Custom xref widgets (FK/M2M/JSON) | ✅ | ❌ manual FK resolve | ✅ `widgets.py` |
| xref ledger / cross-ref | ✅ GenericFK `Assets` ledger | 🟡 string `Resource` + per-load cache | ✅ `Resource` ledger + `resolve_xref` |
| Frozen-tier policy | ✅ `FROZEN_BUCKETS` | ❌ | ✅ `FROZEN_TIERS` |
| depends_on ordering | ✅ toposort | ❌ | ✅ `ordering.order_entries` |
| xref collision detection | ✅ | ❌ | ✅ (`managers._check_xref_collisions`) |
| Adopt existing target (unique-field) | ✅ | 🟡 `_natural_lookup` | ✅ `_adopt_existing_target` |
| URL / remote resources | 🟡 modeled, raises | ❌ | ✅ `fetch.fetch_url` (content-addressed cache) |
| Binary resources | 🟡 modeled, raises | ❌ | 🟡 detected, raises "not implemented yet" |
| Dry-run | ✅ rollback | ❌ | ✅ `DryRunRollback` |
| rebac relationship loading | 🟡 generic `_write_rebac` | ✅ first-class loader path | 🟡 via `system_context` + install yaml rows |
| Demo-tier write gating | ❌ | ✅ DEBUG/allow_non_dev | ✅ DEBUG/allow_non_dev |
| Mgmt command | 🟡 `angee assets validate/load` | 🟡 `angee assets validate/load/diff` | ✅ standalone `angee_resources validate/load/diff` (+`--dry-run`) |
| Dedicated tests | 🟡 | ❌ none for loader | ✅ `resources/tests/` |

**Evolution highlights:** rename happened in two stages (P0 all-assets → P1
half-renamed → Current fully resources); manifest ownership moved TOML →
`AppConfig`; ledger lost its GenericFK in P1/Current; **P1 lost** ordering, xref
collisions, frozen tiers, dry-run, auto-field restore — all **restored** in
Current; Current is the only repo with working URL fetch and a standalone
`angee_resources` command.

---

### GraphQL

**Approach & evolution.** P0 is a pure codegen machine — writes Python source per
app (`runtime/<app>/schema/{types,schema,mutations,aggregates,subscriptions}.py`)
merged by multi-inheritance into one `AngeeSchema`; one schema, no opaque-ID
scalar, REBAC via the old `objects.with_actor(user)`. P1 is the richest and
mid-rebuild: still emits per-app `graphql.py` from a metadata IR but assembles
in-process — native relay `connection`/`node`/`mutations`, bespoke
`DeletePreview`/`Revisions`/`search`, decorated verbs, a channels change-stream,
an aggregates bridge, a real `Sqid` scalar over `relay.GlobalID`, and ambient-actor
REBAC via `AngeeRebacExtension` + `RebacDjangoOptimizerExtension`. Current is a
reset to the smallest seam — lifted only schema-merge + `crud` + `changes`, and
added genuine **multiple named schemas** (the one capability neither predecessor
had); no per-model codegen, no aggregates, no opaque scalar, no optimizer.

| Capability | P0 | P1 | Current |
|---|---|---|---|
| Schema merge | ✅ MRO multi-inherit (`schema_emitter`) | ✅ `merge_types` + collision gate (`graphql/schema._assemble`) | ✅ `merge_types` per named bucket, fail-fast (`graphql/schema.build_schema`) |
| crud shortcut | ✅ (generated list/types/mutation) | ✅ full (connection/node/create/update/delete→preview/search/revisions) | 🟡 **mutation-only** `crud()` — no query/list/detail (`crud.py`) |
| changes shortcut | ✅ per-model topic + events | ✅ `on<Model>Changed` + events, rebac-gated | ✅ `changes(Model, field=)` rebac-gated, field-redacted (`subscriptions.py`) |
| SDL emit | ✅ `.angee-schema.graphql` | ✅ `schema.graphql` | ✅ per-name `runtime/schemas/<name>.graphql` |
| Subscriptions (channels) | ✅ broker; consumer in consumer's asgi | ✅ `AngeeGraphQLWSConsumer` at single `graphql/` | ✅ per-name `graphql/<name>/` consumer (`base/asgi.py`) |
| Dataloaders / N+1 optimizer | 🟡 defaults | ✅ `RebacDjangoOptimizerExtension` | ❌ none wired |
| Aggregates / group-by | ✅ emitted | ✅ in-proc bridge | ❌ |
| Sqid / opaque-ID scalar | 🟡 `id` returns `str(sqid)` | ✅ real `Sqid` scalar over `relay.GlobalID` | 🟡 model-side `public_id` only, exposed as plain str |
| REBAC-scoped resolvers | 🟡 `with_actor` | ✅ ambient-actor extension + manager scope | 🟡 model manager + per-mutation `permission_classes`; subscription gating only |
| Codegen (per-model FE types) | ✅ | ✅ (`graphql/emit.py`) | ❌ addons hand-write types |
| Named schemas (multiple) | ❌ single | ❌ single (`name` arg reserved) | ✅ first-class (default `public`; example ships `public`+`console`) |

**Evolution highlights:** the `graphql.py` convention moved from "export root
classes" → Current's "declare named-schema **parts** mapping". P1's rebuild
replaced runtime `type()` metaprogramming with readable emitted source and unified
relay identity on the sqid. Current lifted only the composition seam + mutation-only
crud + rebac-gated changes + per-name serving; **still missing** codegen, query/
connection generation, aggregates, opaque scalar, optimizer, resolver-layer REBAC
extension. Identity handling simplified from P1's relay GlobalID back toward a bare
`public_id` string.

---

### REBAC / Authorization

**Approach & evolution.** All three delegate the engine to standalone
`django-zed-rebac` (P0 `0.1.4` → P1 `0.8.0` → Current `>=0.9.0[strawberry-django]`);
none used guardian/rules/custom ACL — P0's `docs/PERMISSIONS.md` (1113 lines)
explicitly surveys and rejects them. The framework job is constant: discover each
addon's `.zed`, concatenate deterministically, let the library load/evaluate; Angee
adds reserved roles, an actor resolver, the `Meta` bridge. P0 was a sophisticated
*blueprint* + partial skeleton (file emission done, sync/scoping deferred); P1 is
the fully realized implementation; Current has lifted only the **compose-side glue**
(`base/compose/rebac.py`, `RebacMixin` base, manager `system_context`, settings
wiring) — the base addons carrying `.zed` schemas, reserved roles, and GraphQL
enforcement have not yet landed.

| Capability | P0 | P1 | Current |
|---|---|---|---|
| zed-rebac engine | ✅ 0.1.4 | ✅ 0.8.0 | ✅ >=0.9.0[strawberry-django] |
| Per-addon schema merge | 🟡 opaque concat (`builder/permissions.py`) | ✅ `_emit_permissions` dedup by path (`rebac.zed`/`rebac/` dir) | ✅ `iter_permission_paths`/`write_permissions` (`permissions.zed`) |
| Reserved roles | 🟡 schema-only `angee/system#admin` | ✅ `angee/role:admin` + `auth/role:identity_admin` materialized + signal-synced | ❌ not in base tree yet |
| Actor resolver | 🟡 GraphQL-layer mirror | ✅ `auth/actors.resolve` chain (ApiKey/Service → default) | 🟡 engine default middleware only |
| Manager read-scoping | 🟡 `with_actor`/`sudo` | ✅ `RebacManager` ambient `current_actor()` | 🟡 `AngeeModel(Timestamp, Rebac)` gives scoped manager; unscoped until `rebac_resource_type` |
| Instance write-check | 🟡 engine signals + sudo | ✅ signals + clean GQL error map | 🟡 engine signals; loads wrapped in `system_context` |
| `rebac_resource_type` | ✅ via `AngeeRebacMeta` | ✅ `_META_PASSTHROUGH_KEYS` | ✅ documented contract on `AngeeModel` |
| Build-time sync | 🟡 deferred | 🟡 test runner + post_migrate backfill | ✅ `sync_permissions()` after migrate (`pipeline.py:82`) |
| SpiceDB backend | 🟡 engine-provided | 🟡 engine-provided | ✅ explicit `REBAC_BACKEND="local"` + field-read redact settings |
| GraphQL enforcement | 🟡 `require_permission` + `scope_queryset` | ✅ `AngeeRebacExtension` + denial map + field redaction | 🟡 subscription field-visibility only; no extension/query gate |

**Evolution highlights:** engine was always zed-rebac (no auth-model pivot). Schema
filename drifted `permissions.zed` → `rebac.zed` → back to `permissions.zed`; unified
artifact renamed `runtime/.angee-permissions.zed` → `runtime/permissions.zed`.
Reserved roles matured in P1 (universal admin auto-synced from `is_superuser`, full
row materializer) and are absent in Current pending the base-addon lift. Enforcement
shifted P0 explicit gates → P1 ambient-actor row-scoping. Build-time DB sync is only
**fully wired in Current** (`compose/pipeline.py:82`). Field-level redaction:
absent P0, present P1, configured Current.

---

### Model Layer, Mixins & Field Types

**Approach & evolution.** P0 is capability-complete for mixins — metaclass
auto-installs a `django-sqids` field from `sqid_prefix`, an `audit/` package wraps
`django-simple-history` behind a normalized API, but **no** `StateField`/
`EncryptedTextField` and an orphaned `revisions/` (`.pyc` only). P1 keeps that and
**adds** the field-library bindings P0 lacked (`StateField` via choices-field,
`EncryptedTextField` via cryptography Fernet+HKDF), and **replaces** simple-history
with a custom snapshot/revert `revisions.py` (immutable `<Model>Revision` tables);
simple-history regresses to an unwired marker. Current is a **strip-down**: only
`TimestampMixin`, `AngeeModel` (composing `rebac.RebacMixin`, no metaclass), and a
**lookup-only `SqidMixin` stub**; metaclass, sqid field auto-install, StateField,
EncryptedTextField, HistoryMixin, revisions all gone. New: a polymorphic
`public_id`/`from_public_id` identity seam.

| Feature | P0 | P1 | Current |
|---|---|---|---|
| SqidMixin / opaque IDs | ✅ metaclass auto-installs `SqidsField` | ✅ same | 🟡 **lookup-only stub** — no field auto-emission |
| HistoryMixin + simple-history | ✅ emitted `HistoricalRecords()` | 🟡 marker only (unwired) | ❌ |
| Revisions (custom) | ❌ orphaned dead code | ✅ snapshot/revert + emitted tables | ❌ |
| StateField (choices-field) | ❌ | ✅ `core/fields.py` | ❌ (plain TextChoices) |
| EncryptedTextField (cryptography) | ❌ | ✅ Fernet+HKDF, `secret=True`, rotation | ❌ |
| Audit (created_by/updated_by) | ✅ `SET_NULL` | ✅ `PROTECT`, indexed | ❌ |
| Base / timestamp mixin | ✅ `TimeStampedMixin` | ✅ `TimestampedMixin` | 🟡 `TimestampMixin` (indexed) |
| Abstract source-model base | ✅ metaclass-tagged | ✅ `AngeeModel` + ~40-key `AngeeMetaConfig` | 🟡 `AngeeModel(Timestamp, Rebac)`, classmethods only |
| Model extension (`extends`) | ✅ MRO roles | ✅ `Meta.extends` validated | ✅ `get_extension_target`/`get_extension_bases` |

Other mixins: **P0** also `ColorIcon`/`Starred`/`Taggable`; **P1** also
`Archivable`/`ColorIcon`/`Starrable`/`Subscribable`/`Taggable` + `AvatarField`/
`IconField`; **Current** none of these.

**Evolution highlights:** sqid binding regressed to a lookup stub in Current
(field must be hand-declared); two competing history strategies (P0 simple-history,
P1 custom revisions) and Current has **neither**; field-library bindings peaked in
P1 and were removed in Current; metaclass deleted in Current in favor of plain
abstract bases + classmethods, folding REBAC in by composing `RebacMixin` directly.

---

### Auth / Identity / OIDC

**Approach & evolution.** P0 = two blocks (`django-angee-auth` +
`django-angee-auth-oidc`); OIDC client hand-rolled on `requests` + `pyjwt[crypto]`,
GraphQL handshake. P1 = the matured form and the lift target: identity (`auth`)
cleanly split from OAuth/OIDC, which moves into a credential-source addon
`integrate_auth_oauth` built on a neutral capability addon `integrate`; OIDC rebuilt
on `httpx` + `pyjwt[crypto]` with hardened verification (alg-allowlist, issuer
pinning, nonce, PKCE, discovery caching, typed errors); actor resolver chains
ApiKey/Service in front of rebac's default; OIDC SSO provisions users via an
`oauth_identity` manager *contributed onto* `auth.User` via `Meta.extends`.
**Current: auth has NOT been lifted** — no User model, no auth addon, no OIDC/OAuth;
`pyjwt`/`httpx` aren't even dependencies. Only traces: `base/settings.py` wires
`django.contrib.auth`/sessions + rebac backend/middleware; subscriptions consume
`rebac.actors.get_actor_resolver()`; `docs/stack.md` declares the intended future
binding.

| Capability | P0 | P1 | Current |
|---|---|---|---|
| User / identity model | ✅ `User` + Group/Service/ApiKey/Scope | ✅ `User` (+preferences/avatar) + Group/Service/ApiKey/Impersonation | ❌ |
| Session handling | ✅ Django session via oidc mutations | ✅ + impersonation keys, async-aware | 🟡 middleware wired only |
| OIDC discovery | ✅ (requests, no cache) | ✅ (httpx + cache + validation) | ❌ |
| OIDC token exchange | ✅ | ✅ + PKCE | ❌ |
| JWT verify (pyjwt) | 🟡 no issuer pinning | ✅ + issuer pinning, typed errors | ❌ (no dep) |
| OAuth providers | 🟡 `OidcProvider` (plaintext secret) | ✅ `OAuthProvider` (encrypted, OIDC+OAuth2, SSO policy) | ❌ |
| Actor resolver → REBAC | 🟡 bridge, resolver TODO | ✅ `auth/actors.resolve` chain | 🟡 consumes rebac default only |
| Login GraphQL/REST | ✅ codegen ops + resolvers | ✅ `lift()` verbs + decorated manager methods | ❌ |
| Frontend login UI | ✅ LoginPage/Form/AccountMenu | ✅ LoginPage + `AUTH_LOGIN_SLOT` | ❌ |
| Integrate addon pattern | ❌ (oidc coupled to auth) | ✅ neutral `integrate` ← `integrate_auth_oauth` via `Meta.extends` | ❌ |
| Compat shim | ✅ replaces `django.contrib.auth.models` | ✅ matured | ❌ (uses contrib.auth normally) |

**Lift order for Current:** (1) add `pyjwt[crypto]` + `httpx` deps; (2) `auth`
addon (User/Group/Service/ApiKey/Impersonation, compat shim + early shim-install
hook in `compose_defaults`, `actors.resolve` + `REBAC_ACTOR_RESOLVER`, password
backend, signals, auth GraphQL verbs, `auth/web` LoginPage + slot); (3) `integrate`
neutral addon; (4) `integrate_auth_oauth` with `oidc/` + the two `Meta.extends`
contributions. Prerequisite plumbing (`Meta.extends`, `lift()`/decorated-manager
GraphQL) must already exist in Current's `base/`.

---

### Storage

**Approach & evolution.** P0 (`blocks/storage/`) = six abstract models + a `registry`
shim + a **static React UI mockup** (fixture data, no real upload); no presign, no
MIME, no REST/GQL, no commands. P1 (`src/angee/storage/`) is the full predecessor:
`StorageBackend` abstraction (local + S3/R2/MinIO) with presigned-or-proxy upload,
libmagic MIME detection + signature fallback, a 3-verb django-ninja REST sidecar +
parallel GraphQL surface, content-hash dedup, soft-delete/trash, a
`prune_storage_files` GC command, and a complete React upload widget. **Current has
NOT lifted storage** — zero hits for presign/magic/ninja/upload; only `docs/stack.md`
intent rows (django-ninja sidecar, python-magic, react-dropzone).

| Capability | P0 | P1 | Current |
|---|---|---|---|
| Backends abstraction (local, S3) | ❌ (string fields only) | ✅ `StorageBackend` + Local/S3 + resolver | ❌ (stack.md owner only) |
| Presigned upload flow | ❌ | ✅ begin/proxy/finalize + presign-or-proxy | ❌ |
| MIME detect (python-magic) | ❌ | ✅ libmagic + signature fallback | ❌ |
| File/attachment model | 🟡 abstract, no behavior | ✅ `Backend/Drive/Folder/File/FileAttachment` + state/dedup/trash | ❌ |
| REST sidecar via ninja | ❌ | ✅ `rest/router.py` (begin/upload/finalize) | ❌ |
| GraphQL types | ❌ | ✅ `graphql/type_defs.py` + decorated verbs | ❌ |
| Upload widget frontend | 🟡 static mockup | ✅ `web/.../fileUpload.tsx` over base `Dropzone` | ❌ |
| Mgmt commands | ❌ | ✅ `prune_storage_files` (GC) | ❌ |

**Evolution highlights:** P0→P1 is the qualitative leap (models+mockup → full
runtime); model shape changed (dropped renditions/public/FolderFile, added `Backend`
model + smart-folder trash); dual REST+GraphQL over one manager core; presign-or-proxy
is backend-driven; MIME has graceful fallback (and correctly avoids
try/except-on-import-as-flag). **`react-dropzone` is aspirational across all three** —
P1's real widget uses a hand-rolled native `useDropzone` primitive; a divergence with
`stack.md` to reconcile when the frontend is lifted.

---

### Frontend Framework (SDK + Base Binding)

**Approach & evolution.** P0 ships a **monolithic** package already named `@angee/sdk`
(`blocks/angee/ui/react/`) — shells/views/graphql/auth/router/widgets/slots in one
tarball; composition is **runtime registration** (global `registry/store.ts` +
imperative `declareSlot`/`attach`); react-router 7, Radix + `@base-ui/react`, CVA,
TipTap, hand-rolled command palette. P1 is the realized split the locked stack
describes: headless `@angee/sdk` + single rendered binding `@angee/base`
(`@base-ui-components/react` + tailwind-variants); composition flipped to
**build-time declarative** `defineAddon` + `createApp` (fail-fast collisions),
TanStack Router/Form/Table/Virtual, lucide, `cmdk`. **Current has lifted zero
frontend** — `src/angee/base/` is Python-only; no `.ts`/`.tsx`, no `package.json`,
no `pnpm-workspace.yaml`. Frontend exists only as `docs/stack.md` + `docs/frontend/
guidelines.md` intent.

| Capability | P0 | P1 | Current |
|---|---|---|---|
| Headless SDK | 🟡 monolith (not separated) | ✅ dedicated `packages/sdk` | ❌ |
| urql provider stack | 🟡 auth+persisted+csrf exchanges | ✅ `GraphQLProvider` + `createUrqlClient` | ❌ |
| graphql-ws subscriptions | 🟡 | ✅ subscriptionExchange + retry + zookie | ❌ |
| Codegen / typed ops | ❌ | ✅ `sdk/codegen.ts` → resource type maps | ❌ |
| valibot binding | ❌ (`@standard-schema/spec`) | 🟡 field-level only | ❌ |
| defineAddon | 🟡 runtime `AngeeApp` class | ✅ build-time manifest | ❌ |
| createApp | ❌ manual host wiring | ✅ `base/src/createApp.tsx` | ❌ |
| Route composition | 🟡 react-router 7 | ✅ TanStack Router | ❌ |
| Slot system | ✅ runtime declare/attach | ✅ build-time merge + `useSlot` | ❌ |
| ListView | ✅ | ✅ | ❌ |
| BoardView | 🟡 named Kanban | ✅ | ❌ |
| FormView | ✅ | ✅ + rich form-layout | ❌ |
| Shell / chrome / layout | ✅ | ✅ Console/Public shells + chrome + layouts | ❌ |
| Primitives / UI kit | ✅ Radix+CVA (~50) | ✅ Base UI + tailwind-variants (~50+18) | ❌ |
| Tokens / theming | ✅ SCSS themes | ✅ semantic Tailwind tokens | ❌ |
| i18n | ✅ react-i18next per-block | ✅ per-addon namespace merged by composeAddons | ❌ |
| Command menu | 🟡 hand-rolled (no cmdk) | ✅ `cmdk` Spotlight | ❌ |

**Evolution highlights:** `@angee/sdk` name is **not new** (P0 used it for the
monolith); P1's contribution is the **sdk/base split**. Biggest shift: runtime
registration → build-time `defineAddon`/`createApp` with fail-fast collisions
(matching the "compose at build time, no runtime registration" rule). Router swap
react-router → TanStack; styling swap Radix/CVA/SCSS → Base UI/tailwind-variants/
tokens; codegen is a P1 addition; valibot still partial even in P1. The
`pnpm-workspace` globs in P1 (`src/angee/*/web`, `examples/*/src/web`) show where
lifted addon frontends will land in Current.

---

### CLI, Dev Supervisor & Transport Wiring

**Approach & evolution.** In all three the user-facing `angee` CLI is the **same
external Go/Cobra binary** (`github.com/fyltr/angee`, Cobra root
`internal/cli/root.go`); Django never owned `dev`/`init`/`ws`. The Go binary owns
init/dev/workspace/operator/templates/stack/service/job/secret; `manage.py angee …`
owns only build-system actions. `melos.yaml` in P0 is unrelated (Flutter workspace
tool). `angee dev` existed in **all three**, driven by **process-compose** from a
generated `process-compose.yaml`. Clearest evolution: transport moved
`runserver` (P0) → `daphne` ASGI (P1/Current); GraphQL WS routing moved from a
hand-written single consumer in the **consumer's** `host/asgi.py` (P0) → a
framework-owned single-schema wrapper (P1) → framework-owned **per-schema-name**
router `graphql/<name>/` (Current); settings/urls ownership climbed from consumer
`host/` (P0) → `angee.core` (P1) → `angee.base` (Current).

| Capability | P0 | P1 | Current |
|---|---|---|---|
| `angee` CLI binary | ✅ external Go/Cobra | ✅ same | ✅ same (`/usr/local/bin/angee`) |
| `angee dev` supervisor | ✅ build-watch + runserver:8100 + Vite:5173 | ✅ + **daphne** + Storybook + operator | ✅ daphne, `StackDevForeground` process-compose |
| `angee init` | ✅ | ✅ | ✅ `--dev`/`--input` Copier |
| `angee ws`/workspaces | ✅ (`dev-pr`, `dev-pr-multi`) | ✅ (+ `agent-default`, live workspaces) | ✅ create/status/get/list/destroy/git/push/sync-base |
| Copier templates | ✅ stacks + workspaces | ✅ + staging + `services/claude-code`/`opencode` | 🟡 slimmed to `stacks/dev` + `workspaces/dev` |
| Django mgmt commands | 🟡 `angee build/doctor/migrate/assets` | ✅ `angee build/doctor/migrate/clean/export-graphql-schema/assets` | 🟡 split: `angee build/clean` + `angee_resources` (no watch/migrate/doctor) |
| settings composition | 🟡 consumer hand-writes `host/settings.py` | ✅ `angee.compose.settings` | ✅ `angee.base.settings.compose_defaults` returns full stack |
| asgi/channels subscription mount | 🟡 consumer `host/asgi.py` single route | 🟡 `angee.graphql.asgi.wrap_asgi_application` single route via `ANGEE_ASGI_WRAPPER` | ✅ `angee.base.asgi.build_application` **per-schema** routes |
| urls composition | 🟡 consumer `host/urls.py` | ✅ `angee.core.urls` (+ Ninja routers) | ✅ `angee.base.urls` per-name `graphql/<name>/` |

**Evolution highlights:** CLI never lived in Python/melos. `angee dev` existed from
P0 (web transport changed runserver → daphne). Supervisor is process-compose
throughout. The `build --watch` handshake (`ready (cycle 1)`) persisted P0→P1 but
**was removed from Current's Python command** while the dev template still calls
`angee build --watch --no-apply` — **drift to flag**. GraphQL transport climbed the
stack and went multi-schema. REBAC actor resolution on WS connect present in P1 +
Current. Mgmt-command surface and template catalog peaked at P1 and were trimmed in
Current.

---

### Testing & Quality Tooling

**Approach & evolution.** Shared backend spine throughout (`pytest`+`pytest-django`,
ruff/mypy from `pyproject.toml`), but the harness narrowed sharply. P0 is most
complete: a powerful `synthetic_project` conftest fixture (materializes a throwaway
consumer + synthetic blocks, runs `angee build`), deep unit/integration split, full
Vitest suites (64 React tests), two-job CI. P1 keeps backend breadth, promotes the
YAML-scenario runner to a shipped `angee.testing` addon (pytest11 entry point),
**drops the synthetic-project fixture**, replaces Vitest with Node `node --test`
(~140 lint-style "policy" files), adds Storybook, and its only CI workflow
(`golden-path.yml`) is broken/stale. Current is a stripped early refactor:
backend-only, no frontend tooling, no Storybook, no CI, no scenario runner, no
synthetic-project fixture — composer tested by directly importing
`angee.base.compose` internals against a static `tests/settings.py`.

| Capability | P0 | P1 | Current |
|---|---|---|---|
| pytest + pytest-django | ✅ | ✅ (via `compose_defaults` host.settings) | ✅ (static `tests.settings`) |
| Synthetic-project fixture | ✅ flagship | ❌ removed | ❌ none |
| Integration fixtures | ✅ dedicated `integration/` | 🟡 scenario runner + example host | 🟡 db resource-load tests; example rebac test outside testpaths |
| Testing addon / scenario harness | ✅ `testing/` + pytest11 | ✅ promoted `src/angee/testing` (+query-budget) | ❌ |
| Vitest (frontend unit) | ✅ 64 tests | ❌ `node --test` policy audits | ❌ |
| Playwright (e2e) | ❌ | 🟡 referenced but stale/broken | ❌ |
| Storybook | ❌ | ✅ `storybook-host` + base-previews | ❌ |
| ruff | ✅ | ✅ | ✅ (strictest line-length, no E501 ignore) |
| mypy | 🟡 most machinery (django plugin+stubs) | 🟡 looser (`ignore_errors` overrides) | 🟡 minimal (no plugin/stubs) |
| CI workflows | ✅ two-job working | 🟡 one broken/stale | ❌ none |

**Evolution highlights:** the synthetic-project fixture is a **P0-only asset** (not
carried forward) — Current tests the composer by importing emission internals, far
lower fidelity. The YAML scenario runner survived P0→P1 then **lost in Current**
(regression in integration coverage). Frontend test character drifted behavioral
(P0 RTL) → structural lint-gates (P1 policy) → nothing (Current). CI maturity
declined and is partly broken; determinism gating is P1-CI-unique. Current's
coverage (~11 backend files) is materially thinner than P1's (43 backend + ~140 JS +
Storybook), consistent with an early base-lift.

---

## 4. Current base-lift status & remaining roadmap

What the `wip-base-lift-refactor` branch has lifted from P1 (the source of truth),
and what remains — in rough dependency order.

**Lifted (present in `src/angee/base/`):**
- Composer core seam (discovery, toposort, emission of models, `compose_defaults`,
  tempdir `--check`, manifest) — simplified vs P1 (no metaclass, no admin/apps
  codegen).
- Resources — best-of-three (import-export restored + URL fetch + full rename).
- GraphQL composition seam — schema merge, mutation-only `crud`, rebac-gated
  `changes`, multiple named schemas, per-name HTTP+WS serving + SDL drift check.
- REBAC compose-side glue — Zed concat + `sync_permissions`, `RebacMixin` base,
  `system_context`, settings.
- CLI/transport — `compose_defaults`, `base/asgi.py` per-schema routes,
  `base/urls.py`, `angee`/`angee_resources` commands.
- Model base — `TimestampMixin`, `AngeeModel`, `extends` support, `SqidMixin`
  lookup stub.

**Not yet lifted (pending):**
1. **Model/field bindings** — sqid field auto-emission (currently lookup-only),
   `StateField`, `EncryptedTextField`, audit/created-by, history/revisions, the
   other mixins (Archivable/ColorIcon/Starrable/Subscribable/Taggable). Add
   `cryptography`/`django-choices-field` deps.
2. **GraphQL depth** — per-model codegen, query/connection/node generation, `search`,
   `DeletePreview`, aggregates, opaque `Sqid` scalar, dataloader optimizer,
   resolver-layer `AngeeRebacExtension`.
3. **REBAC base addon** — `.zed` schemas, reserved roles + materializer + superuser
   signal, custom actor resolver, GraphQL enforcement extension + denial map.
4. **Auth stack** — add `pyjwt[crypto]`+`httpx`; lift `auth` addon (User/Group/
   Service/ApiKey/Impersonation, compat shim + early-install hook, password backend,
   signals, auth GraphQL verbs, `auth/web` LoginPage+slot); then `integrate`; then
   `integrate_auth_oauth` (oidc client + `Meta.extends` contributions).
5. **Storage addon** — backends, presigned/proxy upload, MIME, REST sidecar +
   GraphQL, GC command, upload widget.
6. **Frontend** — the entire `@angee/sdk` + `@angee/base` split, `defineAddon`/
   `createApp`, views, shell/chrome, tokens, i18n, command menu.
7. **Testing/CI** — synthetic-project fixture (P0-only, never ported), scenario
   runner (P1, lost), Vitest/Playwright/Storybook, CI workflows.

---

## 5. Drift & risks to reconcile

- **`angee build --watch` drift:** Current's dev stack template still invokes
  `angee build --watch --no-apply`, but Current's Python `angee` command dropped
  `--watch`. (`templates/stacks/dev/.../angee.yaml.jinja` vs
  `src/angee/base/management/commands/angee.py`.)
- **`.zed` filename convention** drifted `permissions.zed` → `rebac.zed` → back to
  `permissions.zed`; when lifting auth/core `.zed` from P1 (`rebac.zed`), rename to
  match Current's `BaseAddonConfig.rebac_schema = "permissions.zed"`.
- **Manifest input hash** present in P1, absent in Current's `.angee-manifest.json`
  — reduces drift-detection fidelity.
- **`react-dropzone`** is a stack.md choice that no repo actually uses (P1 uses a
  hand-rolled `Dropzone` primitive) — reconcile the doc vs reality when lifting the
  frontend.
- **Synthetic-project test fixture** (P0-only) is the highest-fidelity composer
  test and was never ported; Current's "import emission internals" approach is a
  lower-fidelity substitute worth upgrading.

> Note: P1 was mid-rebuild in several areas (GraphQL "checkpoint graphql rebuild
> state"; auth "THIN-addon" refactor); some P1 code carries "verify on first build"
> spike markers. Treat P1 as the richest reference, not a frozen spec.
