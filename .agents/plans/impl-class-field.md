# Plan: a platform-level `ImplClassField` for the impl-class pattern

Status: **implemented + verified.** Final design: `ImplClassField` is a
`TextChoicesField` whose enum is built from a composed Django setting
(`registry_setting`) mapping short keys → dotted impl paths; addons contribute via
`autoconfig`. `strawberry-django` renders the native GraphQL enum. The registry
must be **non-empty**, so an addon with an otherwise-empty set ships a noop
default (storage `local`; integrate `none` → `NoopVCSClient`). Decision history:
registry-key (§4) → briefly `__subclasses__()` → settings-dict → enum-backed
(this final form, on the architect's "make it an enum" + "noop dummy" calls).
This session implemented the shared field + `storage.Backend` adoption + the doc
rule; a parallel agent built the `integrate` adoption (`VCSIntegration`), and the
shared field's non-empty-registry contract is satisfied there by the noop.
Handover: `.agents/handovers/impl-class-field.md`.

## 1. What forced this

Several rows in the platform point at a Python implementation class — a
strategy/client/backend that is **not** a Django model — chosen per row. Each
site hand-rolls the same four moves: a `CharField`, an `import_string`, an
`issubclass` check, and (sometimes) an instance cache. We want one base-level
field that owns the pattern, plus a written rule for *when* to use it versus the
two neighbouring patterns it is easy to confuse with.

## 2. Inventory — every site, classified

Three classification buckets:
- **(a)** impl-class-per-row → the new field's job.
- **(b)** closed framework-known kind → enum + handler registry; leave.
- **(c)** model-subclass discovery → model registry; leave.

The full sweep (`grep import_string`, `*_class = models.`, `register_*`,
`__subclasses__`) found exactly the sites below — there are only **three**
`import_string` call sites in `angee/` + `addons/`.

| Site | Shape today | Class |
|---|---|---|
| `storage.Backend.backend_class` (`storage/models.py:111`, resolved `:162`) | `CharField(200)` → `import_string` + `issubclass(StorageBackend)` + `(pk, frozen config)` instance cache + `resolved_config()` env expansion | **(a)** — the canonical case |
| `integrate.Integration.impl_class` (`integrate/models.py:172`, resolved `:210`) | `CharField(200, blank)` → `import_string(self.impl_class)(self)`; resolves a `GitHost` | **(a)** — greenfield; not exposed in GraphQL, no rows written yet |
| `iam.credentials` `CredentialKind` + `register_handler`/`handler_for` | closed `TextChoices` (oauth/static_token/ssh_key) + eager handler registry; `Credential.kind` is a `StateField`, projected as a GraphQL enum | **(b)** — keep |
| `integrate/registry.py` `capability_models`/`bridge_models`/`source_models` | `issubclass` scan over `apps.get_models()` | **(c)** — keep |
| `angee/graphql/schema.py:395` `import_string(dotted_path)` | resolves an addon's `schemas` attribute (a dotted reference declared on a **trusted `AppConfig`**) | **out of scope** — build-time declaration reference, not a writable row, not untrusted input. "App facts live on `AppConfig`." Leave. |
| `integrate/vcs/host.py` `GitHost`, `integrate_github/host.py` `GitHubHost` | the impl classes site (a)/integrate will reference | context — they become the **registered impls** |

### Why (b) and (c) are *not* the new field

- **(b) `CredentialKind`** is a **closed** set the framework itself owns
  (three kinds, a `TextChoices`). The row stores the *enum value*, never a class
  name; the handler is selected by enum, and the enum projects as a GraphQL enum
  through `StateField`. This is the right tool for a finite, framework-known set.
  It is the **closed-set analogue** of the new field's open-set registry —
  document the boundary, do not merge them.
- **(c) `*_models()`** discovers concrete **model** subclasses (different
  persisted fields per `Capability`/`Bridge`/`Source` variant) via the Django
  app registry. This is *model polymorphism* — the complement of the new field:
  when variants differ in **persisted fields**, subclass the model; when they
  differ only in **behaviour**, use one model + the new field.

So the new field has exactly **two** adopters: `storage.Backend` and
`integrate.Integration`, with `InferenceIntegration → AnthropicClient`
anticipated as the third.

## 3. Research — does Django or a library already own this?

- **Django built-ins** — there is *no* first-class "reference to a Python class"
  field. The documented idiom is exactly what we have:
  `CharField` + `django.utils.module_loading.import_string`. Confirmed; this is
  why every site hand-rolls it.
- **`django-typed-models`** — single-table typed *models* (a `type` column
  resolves a row to its Python **model** subclass). Targets model polymorphism
  (our bucket (c)), not non-model strategy classes. We are *deliberately
  avoiding* per-provider models (the table/field duplication the handover
  rejects), so this is the wrong tool. Rejected.
- **`django-polymorphic`** — multi-table model polymorphism; fragments the table
  (the exact thing we avoid — we want one `Integration` table for unified
  list/reconcile). Rejected.
- **`django-model-utils` / `django-extensions`** — no maintained "class/path"
  field that fits "row → non-model impl class". `model_utils` offers
  `Choices`/`InheritanceManager` (model polymorphism again). Nothing fits.

**Conclusion:** nothing off-the-shelf owns "a row names a non-model impl class."
Build it at the base level, joining `SqidField`/`StateField`/`EncryptedField`,
which each wrap one concern as a base field. Adding it does not require a new
`docs/stack.md` row (no new dependency — it is glue over Django's own field +
module-loading).

## 4. The central design call — registry-key vs dotted-path

This is the decision that gates everything and **reverses a documented rule**
(`docs/backend/guidelines.md:160-164` currently says
"dotted-path `impl_class` string"), so it is escalated to the architect (§9).

### Option A — typed dotted-path (keep the FQN, wrap + check it)
Store the FQN; the field `import_string`s it and asserts `issubclass(base_class)`.
- ➕ Zero data migration (column stays the same string content).
- ➕ Lazy import — no per-addon eager-registration wiring.
- ➕ Already documented and in use.
- ➖ **Unsafe by default**: `import_string` of writable-row text is a
  code-execution surface. Both adopters gate the write *today* (Backend via
  `StorageAdminPermission`; `Integration.impl_class` is not exposed) — but this
  is **framework code that is copied downstream**, and `integrate/integration`'s
  `write` permission is `owner + admin`. A consumer addon that exposes
  `impl_class` on its `IntegrationPatch` hands a non-admin owner arbitrary
  `import_string`. The foundation should not make that the easy path.
- ➖ Brittle to refactors: the stored FQN couples every row to the impl's exact
  module path; moving `GitHubHost` orphans rows.
- ➖ Bad picker UX: an admin must paste an FQN; nothing can enumerate choices.

### Option B — registry-key (recommended)
Each impl registers itself against its base class under a short, stable key; the
row stores the **key**; the field resolves through the registry.
- ➕ **Safe by construction** — closed allowlist; no `import_string` of row text.
  The pattern the foundation teaches is the safe one.
- ➕ **Picker**: enumerate registered impls → choices for an admin dropdown /
  GraphQL query. Dotted-path cannot do this.
- ➕ **Refactor-stable**: the key survives class moves/renames.
- ➕ Mirrors the blessed `iam.credentials.register_handler`/`handler_for` idiom
  (open-set analogue) and `*_models()` discovery — consistent with the repo.
- ➕ Resolves the constitution's smell directly: the **base class owns its
  impls** (Find the owner), instead of `import_string` decoding a string from
  outside.
- ➖ Needs eager registration so the picker/resolver sees every impl. For
  `storage` this is automatic (`storage/models.py` imports
  `angee.storage.backends`, registering `LocalBackend`). For a host addon the
  one-line cost is an `AppConfig.ready()` that imports its `host` module
  (standard Django signal-wiring shape). Greenfield for integrate.
- ➖ A small **data migration** (FQN → key). Cheap here: storage has one seeded
  row (edit the seed); integrate has none.

**Recommendation: Option B.** The decisive factor is that this is foundation
code copied into every downstream addon: "keep the foundation clean so the code
people copy is the code we want them to write" (`AGENTS.md` → DRY). Safety,
picker UX, and refactor-stability all point the same way; the only costs (a
one-line `ready()` and a one-row seed edit) are trivial and one-time.

## 5. Recommended design (Option B), buildable spec

### 5.1 The registry is a composed Django setting; the field is an enum — IMPLEMENTED
**Decision history:** registry-key → briefly `__subclasses__()` → settings-dict
(plain `CharField`) → **enum-backed `TextChoicesField`** (final, on the
architect's "make it an enum, handled standardly by strawberry-django" + "noop
dummy to avoid empty" calls). The registry is a composed Django setting
(`registry_setting`) mapping short keys → dotted impl paths; addons contribute via
`autoconfig`. The field is a `TextChoicesField` whose `choices_enum` is built from
those keys at model-import time, so `strawberry-django` renders the GraphQL enum
natively (like `StateField`) — no `auto`/type-map hack, no per-adopter resolver.

Why this shape:
- *Composition-native + secure.* The row stores only a key; `import_string` runs
  on the **composed, trusted setting value**, never row text (mirrors
  `schema.py:395` resolving an addon's declared reference).
- *Native enum.* Because every addon has contributed by schema-build time the key
  set is closed, so a `TextChoices` enum is correct; strawberry-django's
  `TextChoicesField` branch renders it.
- *Real system check.* `manage.py check` imports every configured path and
  verifies it subclasses `base_class` (`angee.E001`–`E004`).
- *Project override.* A deployment can remap a key in settings, no code change.

**The non-empty-registry contract (the sharp edge).** A `TextChoices` enum cannot
have zero members, so every registry must have ≥1 impl. An addon whose set could
otherwise be empty ships a **noop/null-object default**: storage `local`
(`LocalBackend`), integrate `none` (`NoopVCSClient`, added to
`integrate/vcs/client.py` + `integrate/autoconfig.py`). Consequence: the enum is
built at *model-import time* from the setting, so **every settings module that
installs the addon must carry a non-empty mapping** — including bare modules that
skip the composer (`tests/settings.py` declares both
`ANGEE_STORAGE_BACKEND_CLASSES` and `ANGEE_VCS_CLIENT_CLASSES`). An empty registry
raises `ImproperlyConfigured` at import. This import-time coupling is the cost of
the native-enum choice over a plain empty-tolerant column.

`deconstruct` drops the registry-derived `choices` and keeps only
`registry_setting` + a fixed `max_length`, so the migration is a stable plain
varchar and adding/removing an impl never churns a migration; the enum is rebuilt
from settings on reconstruct. Storage's mapping lives in `storage/autoconfig.py`;
an extending backend addon adds a yamlconf dotted key
(`"ANGEE_STORAGE_BACKEND_CLASSES.s3": "…"`).

### 5.2 The field — `angee/base/fields.py`
```python
class ImplClassField(models.CharField):
    """A column naming a non-model implementation class by registry key.

    Stores a short stable key (never a dotted path); resolves it against the
    impls registered for ``base_class``. Not a code-execution surface, and the
    registry's choices drive a picker. Parameterized like
    ``StateField(choices_enum=...)``: ``ImplClassField(base_class=StorageBackend)``.
    """
    def __init__(self, *, base_class, registry=impl_registry, **kwargs): ...
    def deconstruct(self):   # pop base_class + registry — not DB-schema facts
        ...                  # so the column stays a plain varchar; migrations stable
    def check(self, **kwargs):   # angee.E0xx if base_class is not a type
        ...
    def resolve_class(self, key):  return self.registry.resolve(self.base_class, key)
    def choices_for_picker(self):  return self.registry.choices(self.base_class)
```

- **`base_class`** is the typed parameter (like `StateField`'s `choices_enum`):
  the field knows the expected base, so the registry guarantees subclass-ness at
  registration and `resolve_class` *replaces both* `import_string` **and** the
  `issubclass` check at the call site.
- **System check vs row validation (clarifying the handover):** Django's native
  E-code mechanism is `Field.check()` (auto-collected by `manage.py check`, no
  `register()`), but it sees only the *declaration* — it cannot see row values.
  So `check()` validates `base_class` is a type (`angee.E0xx`); an invalid/blank
  *row key* is caught at write via `clean()`/`validate` (a `ValidationError`,
  which flows through `extensions.validationErrors` per the backend Pitfalls)
  and at resolve time (clear error). Construction-time `raise` guards a missing
  `base_class`, mirroring `EncryptedField.__init__`.
- **Deconstruct** drops `base_class`/`registry`, so the emitted column is a plain
  `varchar` — identical DB representation; only an `AlterField` (field path
  change) is generated, never a column rebuild. Composer emission is unaffected
  (the field rides on the abstract source model the composer already inherits).

### 5.3 Resolution, caching, construction contract — stay on the model
The two adopters have genuinely different constructor contracts:
`StorageBackend(backend_config=...)` vs `GitHost(integration)`. The field must
**not** guess constructor args. So:
- **Field owns** class resolution (`resolve_class`) + choices — the cheap,
  shared, safe part.
- **Model owns** instantiation + caching — the part that needs the sibling
  config column and the `(pk, frozen config)` identity. `Backend.storage` keeps
  its instance cache and calls `impl(backend_config=self.resolved_config())`;
  `Integration.host()` calls `impl(self)`. This keeps the **domain method on the
  owning model** (handover constraint) and is *less* code than today
  (`resolve_class` subsumes `import_string` + `issubclass`). Class resolution is
  a dict lookup, so only the storage *instance* cache remains — unchanged.

### 5.4 GraphQL boundary
- **Read**: the key projects as a `String` via `auto` — same as `backend_class`
  today.
- **Write**: a `String` input — same as today — but now a bad key fails
  validation instead of silently storing an unresolvable FQN.
- **Picker (new)**: an admin-gated query returning `choices_for_picker()` as
  `[{key, label}]` (the shape a relation picker consumes). This is the UX the
  dotted-path approach cannot offer. Mirror how the console exposes relation
  choices; gate with the same admin permission classes as the owning console.

## 6. Per-call-site migration plan

### `storage.Backend`
1. `backend_class = ImplClassField(base_class=StorageBackend)`.
2. Register impls where they are defined (auto-reachable: `storage/models.py`
   already imports `angee.storage.backends`): `LocalBackend.impl_key = "local"`,
   registered module-level in `backends.py`.
3. `Backend.storage`: replace `import_string` + `issubclass` with
   `self._meta.get_field("backend_class").resolve_class(self.backend_class)`;
   **keep** the `(pk, frozen config)` cache and `resolved_config()`.
4. Data: edit seed `storage/resources/install/010_storage.backend.yaml`
   `backend_class: angee.storage.backends.LocalBackend` → `backend_class: local`.
   Add a data migration to rewrite any existing FQN rows to the key (one row in
   practice).
5. Picker query in `storage/schema.py`, gated by `_STORAGE_ADMIN_CLASSES`.

### `integrate.Integration` (greenfield — no rows, not exposed)
1. `impl_class = ImplClassField(base_class=GitHost, blank=True)`.
2. `integrate_github` registers `GitHubHost.impl_key = "github"`; add
   `IntegrateGithubConfig.ready()` importing `angee.integrate_github.host`
   (one-line, comment the reason — registration on import).
3. `Integration.host()`: `... resolve_class(self.impl_class)(self)` (blank → None).
4. No data migration (nothing stored); `AlterField` only.
5. Picker query in `integrate/schema.py`, gated by the admin permission classes.

## 7. The rule to write into `docs/backend/guidelines.md`

A new entry (and a Pitfalls line), reconciling the existing line 160-164:

> **Choosing how a row selects per-variant behaviour.** Classify by what varies:
> - **Persisted fields differ per variant → subclass the model.** Behaviour +
>   data on the model; discover concrete subclasses via the app registry
>   (`integrate.Capability`/`Bridge`/`Source` + `registry.py`).
> - **Only behaviour differs, OPEN set (addons contribute impls) → one concrete
>   model + `angee.base.fields.ImplClassField`** naming a non-model
>   strategy/client class. One table (unified list/reconcile, no field
>   duplication); the impl is an **explicit per-row** choice, **never** derived
>   from a vendor slug.
> - **Only behaviour differs, CLOSED framework-known set → a `StateField` +
>   eager handler registry** (`iam.credentials.register_handler`/`handler_for`).
>   The row stores the enum value; the kind projects as a GraphQL enum.
>
> **A row-selected impl is stored as a registry key, never a dotted path.**
> `ImplClassField` resolves through the impl registry (the base class owns its
> impls; impls register themselves), so a writable column is not an
> `import_string` code-execution surface and a picker can enumerate the choices.

This **replaces** "put the provider implementation choice on the connection as a
dotted-path `impl_class` string" — the connection still owns the choice and the
catalogue stays pure metadata; only the *representation* changes (key, not FQN)
and resolution moves behind the registry.

## 8a. Storage implementation — verified this session
- `ImplClassField` (settings-dict registry, `angee.E001`–`E004` checks) in
  `angee/base/fields.py`; `storage.Backend.backend_class` adopts it, deleting the
  hand-rolled `import_string` + `issubclass`. Mapping in `storage/autoconfig.py`
  (`ANGEE_STORAGE_BACKEND_CLASSES`); seed + `tests/settings.py` updated; the rule
  + Pitfall in `docs/backend/guidelines.md`.
- Green: `pytest` (329), `mypy angee addons` (113 files), `vulture` (none of the
  new code flagged), `angee build`/`makemigrations storage` (clean `AlterField`,
  no `base_class` leak) /`migrate`/`check`/`schema --check`. Round-trip: a
  `Backend` row with `backend_class="local"` resolves to `LocalBackend`. The
  system check fires E003 (bad import), E004 (non-subclass), E002 (no setting).
- **Known follow-ups:** (1) existing `Backend` rows hold the old FQN — install
  tier is load-once so `resources load` will not rewrite them; real deployments
  need a data migration (`…LocalBackend` → `local`). The example dev row was
  rewritten in place. (2) `integrate.Integration` adoption is owned by a separate
  agent (the VCS work is currently reverted). (3) Pre-existing unrelated ruff
  E501 at `storage/models.py` `File.download_url` (present on `HEAD`) left as-is.

## 8. Verification (when implementation is approved)
- `uv run python -m pytest` (storage + integrate suites at minimum),
  `python -m mypy angee addons`, `python -m ruff check . --no-cache` over
  `angee/base`, `addons/angee/storage`, `addons/angee/integrate`,
  `addons/angee/integrate_github`.
- `uv run examples/notes-angee/manage.py angee build` → `makemigrations storage
  integrate` → `migrate` → `check` (the new `angee.E0xx` fires on a field with a
  bad `base_class`; a bad row key raises a clean `ValidationError`).
- Round-trip: a `Backend`/`Integration` row resolves its impl through the field
  to the same class identity as before; the storage `(pk, frozen config)`
  instance cache and `resolved_config()` env expansion are preserved.
- SDL: `manage.py schema` + `--check` after build (new picker query present).

## 9. Open decision for the architect (gates implementation)
**Registry-key (Option B, recommended) vs typed dotted-path (Option A).** B
reverses the documented dotted-path rule (§7) and costs a one-line `ready()` +
a one-row seed edit; A is zero-migration but keeps the `import_string`
code-execution surface and offers no picker. Everything downstream of this
choice (field shape, migration plan, doc edit) follows from it.
