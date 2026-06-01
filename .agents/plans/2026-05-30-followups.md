# Angee backend — followups & roadmap

Single source for remaining work after the `compose` / `base` / `resources`
restructure landed (green: ruff, mypy, 63 unit + 8 example e2e, `angee build`).
The structural refactor is **done**; this tracks (1) review findings to apply and
(2) the capability build-out, dependency-ordered. Each item names the doc that owns
its rules — consult those, do not restate them here. New capabilities are built to
the guidelines (schema-first, simple model layer, no metaclass) and ship green-field
(no provenance).

---

## 1. Review findings to apply

Synthesis of two review waves (architecture + Django) by Claude / Gemini / Codex.
Deduped and ranked; `[✓verified]` = confirmed firsthand. Reviewer reports are in
`.agents/notes/final-review/`. Fix top-down; a code↔doc mismatch is itself a fix.

### Fix first — correctness & gates
- [ ] **`DeletionPreview` crashes on `SET_NULL`/`SET_DEFAULT` cascades** `[✓verified]`
  — Django 6 `Collector.field_updates` is `defaultdict(list)` keyed by
  `(field, value)`, but `base/deletion.py:59-61` treats it as `{model: {...}}` and
  calls `.values()` on a list. Count by `field.model`; also catch `RestrictedError`
  (not just `ProtectedError`) and include `collector.fast_deletes`; add SET_NULL /
  RESTRICT / fast-delete tests. (codex-django, gemini-django, claude-django)
- [ ] **Wrap the `crud` delete preview + delete in one `transaction.atomic()`** and
  decide whether the cascade preview should be actor-scoped (today the `Collector`
  walk is unscoped — an info-shape issue, not an authz bypass). `crud.py:127-153`.
  (gemini-django, claude-django)
- [ ] **ruff `I001` ships** `[✓verified]` — `resources/management/commands/resources.py:8`
  (local import before django). A warm cache masked it; run `ruff check . --no-cache`
  in the handoff gate. (all three + me)
- [ ] **`angee clean` leaves the runtime uncleanable** — `clean()` deletes the
  generated sentinel in `runtime/__init__.py` while preserving `*/migrations/`, so the
  next `emit()`/`reset()` `_ensure_cleanable()` refuses. Rewrite/keep the sentinel on
  clean, or accept a migrations-only generated layout. `compose/runtime.py:112,349`.
  (codex-django)
- [ ] **`depends_on` string is split into characters** `[✓verified]` —
  `apps.py:336` `tuple(entry["depends_on"])` turns `"seed.csv"` into `('s','e',…)`.
  Treat a bare string as one dependency or reject it. (codex-django)
- [ ] **Structured-row `fields` can override the reserved `_xref`** (mass-assignment)
  — reject `RESERVED_ROW_KEYS` inside `fields`, or merge reserved keys last.
  `resources/entries.py:373,379`. (codex-django)

### Layering / ownership cluster (settle together)
- [ ] **`base` reaches up to `resources` + the build flag is back + `compose` is in
  the run app set** `[✓verified]` — `BaseConfig.source_model_modules =
  ("angee.resources.models",)` makes `base` import `resources`; `import_models`
  sniffs `INSTALLED_APPS` for the literal `"angee.compose"`/`"angee.resources"`
  strings (a reconstructed "is-a-build" flag, slipping past the import-only layering
  test); and `settings._run_installed_apps()` installs `angee.compose` in the
  runtime set. Settle one Resource owner + a clean build/run-settings seam + drop the
  string sniff; extend `tests/test_layering.py` to flag literal sibling-package
  strings in `base`. Reconcile the docs to match. (claude-arch, codex-arch ×2)
- [ ] **GraphQL contract: docs say native Strawberry `Schema` objects, code uses a
  `schemas`-parts dict DSL** — reconcile (decision stands: keep the parts contract,
  update `docs/backend/guidelines.md` to describe it; it exists to merge across
  addons). Doc fix owed. (codex-arch; first review wave)

### Correctness / performance / security
- [ ] **WS GraphQL doesn't enter the REBAC ambient context** — the consumer sets the
  actor only in Strawberry context, not the `ActorMiddleware` ambient context that
  managers/signals use, so non-subscription WS ops can fail strict mode; and
  `_gate_event` runs the sync REBAC backend from the async generator. Add a
  Channels/execution wrapper that brackets WS ops in the REBAC context; run sync
  checks via `sync_to_async(thread_sensitive=True)`. `consumers.py:24`,
  `subscriptions.py:32,48`, `access.py`. (codex-django, gemini-django)
- [ ] **xref resolution: unindexed + `source_addon__endswith` + `.first()`** — define
  one uniqueness contract, index `xref` (leading column), and resolve by canonical
  equality (fail-fast on ambiguity) instead of a suffix scan. `widgets.py:102-111`,
  `resources/models.py`. (codex-arch, codex-django, claude-django)
- [ ] **`resources validate` only checks headers** — run `import_data(dry_run=True,
  raise_errors=True, rollback_on_validation_errors=True)` so FK/xref/widget/model
  validation is exercised, or rename to make header-only scope explicit.
  `managers.py:49`. (codex-arch, codex-django)
- [ ] **N+1 in resource loading** — per-row ledger/target/`update_or_create` +
  per-value widget lookups + per-field gate checks. Preload ledger per entry, cache
  target/xref resolutions per run; investigate a bulk field-access API in the REBAC
  backend. (codex-django, claude-django, gemini-django)
- [ ] **Pin `REBAC_STRICT_MODE=True` in `compose_defaults`** so create/update
  fail-closed is Angee-owned, not inherited from a library default. (claude-django)
- [ ] **`sync_permissions()` exists but isn't wired** to the `angee` command (only
  `build`/`clean`/`schema`) — expose a permission sync/check step or delete the
  wrapper + adjust docs. (codex-arch)
- [ ] **`fetch_url` follows redirects without re-validating scheme/host** — trusted
  build-time path (addon-declared URLs), so harden (validate redirect target, or
  HTTPS-only) rather than treat as Critical. `resources/fetch.py`. (gemini-django;
  claude-django did not flag — reconciled to Medium)

### Decomposition / quality / docs
- [ ] **`signals.py` is loose functions over a module-global `_connected`** — fold
  publishing into a `ChangePublisher` class owning the connected-set, `_on_save`/
  `_on_delete`, and `group_name(model)` (a derived `_meta` fact that wants a method).
  (claude-arch)
- [ ] **`AngeeRuntime._models_source` builds Python by string concatenation** —
  consider AST-unparse or a template; or accept and document the constraint.
  (gemini-arch)
- [ ] **`AngeeResource.import_row` is long/nested** — decompose into ledger-skip /
  adopt / action helpers. (gemini-arch)
- [ ] **`_json_safe` / `_json_default` fall back to `str()`** — raise on unsupported
  types; share one strict serializer. (gemini-arch)
- [ ] **`_adopt_existing_target` infers identity from unique-field shape** — make the
  adoption key explicit on the entry. (codex-arch)
- [ ] **`RevisionMixin.revisions`/`revert_to` typed `Any` + bare `save()`** — type via
  `reversion.models.Version`; `save(update_fields=...)`. (claude-django)
- [ ] **`except TypeError, ValueError:`** (`models.py:87,110`) — parenthesize for
  readability (valid on 3.14, reads as Py2); consider widening ruff to `UP`/`B`. (all)
- [ ] **Low/misc:** `_NativeJSONWidget` naming (cross-module but underscore-private);
  `_history_excluded_fields` heuristic → let the field/mixin declare exclusion;
  publishers connect lazily at first schema build vs `ready()` (confirm intent);
  add a `pyyaml` owner row to `docs/stack.md`; document why `ComposeConfig`/
  `ResourcesConfig` are plain `AppConfig` (command hosts, not addons).

---

## 2. Capability roadmap (dependency-ordered)

The restructure deliberately kept the model layer minimal. These add the capability
breadth the framework is meant to carry. Build each at the level that owns it
(`AGENTS.md` → Repository Role) and wire stack libraries per `docs/stack.md` (add a
dependency only with an owner row).

### 2.0 Composer emission mode — runtime source on disk vs in-memory adopt

Today the composer always writes concrete model source to
`runtime/<label>/models.py` and the source addon adopts it by importing that
module (`docs/composer.md`). Proposal: a setting (e.g.
`ANGEE_RUNTIME_EMIT = "disk" | "memory"`, default `"disk"`) that lets a host skip
writing model source and instead have the adopt hook build the concrete model
classes in process — `type(name, bases, ns)` registered under the addon label,
the same registration the imported file produces, without the file.

How it would work:
- `disk` (current): `ComposeConfig.import_models` writes the tree;
  `BaseAddonConfig.import_models` imports `runtime.<label>.models`.
- `memory`: emission renders the same class objects but registers them directly;
  the adopt hook calls the composer to materialize+register instead of
  `importlib.import_module`.

The asymmetry that constrains the name: **migrations must stay on disk
regardless.** Migration history is persisted, hand-evolved files
(`MIGRATION_MODULES` → `runtime.<label>.migrations`) and cannot be virtualized.
So the setting only governs whether *model source* is written; `makemigrations`
would diff the in-memory concrete models against the on-disk migrations (works,
but the serialized model state comes from runtime-built classes). Name it for
that scope (`...write_models`), not "write runtime".

What `memory` mode costs (why `disk` stays default):
- no reviewable/diffable generated `models.py`; `schema --check` / drift over
  model source no longer applies.
- IDE navigation, stack traces, and `import runtime.<label>.models` break.
- tension with `AGENTS.md` ("Compose at build time. Do not monkey-patch,
  register at runtime") and the §4 invariant "emit-only build (no flag)":
  building model classes via `type()` at import *is* runtime metaprogramming.
  Adopting this means consciously reopening that rule for an opt-in mode.

When it's worth evaluating: ephemeral/serverless boots, teams that refuse to
commit generated code, filesystem-free test composition. Decide whether the
opt-in earns relaxing the no-runtime-metaprogramming rule. The build-time-emit
choice and the prior-art that informs it (the runtime-composition camp — DJP,
Open edX — never emits because it never composes models across addons) are in
`.agents/notes/composer-extraction/prior-art-comparison.md`.

### 2.1 Model & field bindings
- Auto-emit the `sqid` field from a per-model `sqid_prefix` (currently the model
  declares `SqidsField` by hand); the composer owns the emission.
- Convenience mixins: audit (`created_by`/`updated_by`), archivable, color/icon,
  starrable, subscribable, taggable.
- `StateField` (django-choices-field) and `EncryptedTextField` (cryptography) — both
  have owner rows in `docs/stack.md`.
- History (django-simple-history) and a snapshot/revert revision capability — decide
  the owner per `docs/stack.md` and update the revisions row if Angee owns it.
- New deps to add with owner rows: `django-choices-field`, `cryptography`.

### 2.2 GraphQL depth
`base/graphql` currently composes named schemas + a mutation-only `crud()` +
REBAC-gated `changes()`. Add, per `docs/stack.md` (strawberry-django owns types):
- query/connection/node generation and a `search` shortcut on `crud()`.
- aggregation / group-by (strawberry-django-aggregates).
- an opaque id scalar over the model public id.
- a dataloader / N+1 optimizer wired into resolvers.
- a resolver-layer REBAC enforcement extension + denial mapping (today only
  subscription emit is gated).

### 2.3 REBAC base addon
Compose-side glue exists (schema concat, `sync_permissions`, scoped manager). Add a
base addon carrying: `.zed` schemas (filename `permissions.zed` per
`BaseAddonConfig.rebac_schema`), reserved roles + materializer + superuser sync, a
custom actor resolver, and the GraphQL enforcement extension. Authorization stays
out of the model layer (`docs/stack.md`: django-zed-rebac owns it).

### 2.4 Auth / identity
Add `pyjwt[crypto]` + `httpx` (owner rows). Build an `auth` addon (User + Group /
Service / ApiKey / Impersonation, password backend, actor resolver wired to
`REBAC_ACTOR_RESOLVER`, auth GraphQL verbs); then a neutral `integrate` addon; then
an OAuth/OIDC credential addon contributing onto `auth.User` via `extends`
(hardened verification: alg allowlist, issuer pinning, nonce, PKCE, discovery cache).

### 2.5 Storage addon
Backend abstraction (local + S3-compatible), presigned-or-proxy upload, MIME
detection (python-magic), the File/Drive/Folder model set, a django-ninja REST
sidecar + GraphQL surface over one manager core, content-hash dedup + soft-delete,
and a GC management command. All owners are in `docs/stack.md`.

### 2.6 Frontend
The `@angee/sdk` (headless) + `@angee/base` (rendered) split per `docs/stack.md` /
`docs/frontend/guidelines.md`: `defineAddon`/`createApp` build-time composition,
urql provider stack + graphql-ws, TanStack Router/Form/Table/Virtual views
(List/Board/Form), shell/chrome, semantic tokens, i18n, command menu, codegen off
the emitted SDL.

### 2.7 Testing & CI
A synthetic-project fixture that materializes a throwaway consumer + addons and runs
the full build (highest-fidelity composer test, vs today's internal-import tests); a
scenario/integration harness; Vitest + Playwright + Storybook once frontend lands;
and CI workflows running the per-area Checks (`docs/backend/guidelines.md`).

---

## 3. Reconciliations & risks

- **Stale `ANGEE_BUILD` layering prose.** `docs/backend/guidelines.md`
  §Package Layering (the "Build and run use different `INSTALLED_APPS`" bullet)
  still describes the retired two-app-set / `ANGEE_BUILD` split; the code is now
  single-boot emit-then-adopt. `docs/composer.md` is the accurate owner doc —
  reconcile the guidelines bullet to point at it (part of the §1 layering
  settle-together cluster).
- **Dev command surface.** The dev stack template
  (`templates/stacks/dev/.../angee.yaml.jinja`) and the Go `angee` CLI must match
  the management command surface (`angee build` / `clean` / `schema`,
  `makemigrations` / `migrate`, `resources load`). Resolve any `--watch` /
  `--no-apply` / `assets` references the Python side no longer provides; orchestration
  (sequencing, watch, frontend) is the CLI's, not a Python command's.
- **`react-dropzone`** has an owner row in `docs/stack.md` but real upload widgets
  may use a hand-rolled primitive — reconcile the doc vs the implementation when the
  frontend lands.
- **Synthetic-project composer test** — the current composer tests import emission
  internals; a build-the-throwaway-project fixture is higher fidelity (2.7).
- Keep `docs/stack.md` and the manifests in lockstep for every dependency added in
  §2.

---

## 4. Notes for executors

- Restructure invariants to preserve: three-package layering (`base` imports neither
  sibling), emit-only build (no flag, no manifest), `base.Resource` via
  `source_model_modules`, imports-at-top (Django phase-1 exception only),
  compose-onto-classes. See `docs/backend/guidelines.md` → Package Layering.
- Resource adoption is opt-in (`ResourceEntry.adopt`, default off).
- Build new capabilities to the guidelines and verify with the per-area Checks
  before claiming done.
