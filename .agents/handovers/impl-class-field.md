# Handover: a platform-level `ImplClassField` for the impl-class pattern

## Your task

Several places in the platform let a **database row point at a Python implementation
class** (a strategy/client/backend that is *not* a Django model), resolved at
runtime via a dotted path. Each one hand-rolls the same thing: a `CharField`, an
`import_string`, a subclass check, and an instance cache. **Design and introduce
one platform-level field — `angee.base.fields.ImplClassField` — that owns this
pattern, then migrate the existing call sites onto it.**

First **research whether Django or an existing library already provides this**
(see Research below); only build a new field if nothing fits. Deliver an analysis
+ design first (gated on review), then the implementation.

This came up while building the VCS inventory feature (an `Integration` needs to
resolve a host-specific REST client). The decision that forced it:

- Per-provider **models** (`GitHubIntegration`, `AnthropicIntegration`, …) are
  wrong — providers in a domain share the same fields and differ only in behavior,
  so that is pure field/table duplication and fragments the "list all integrations"
  queryset.
- Dispatching the impl by **vendor slug is also wrong** — there can be multiple
  clients/impls for the same vendor across different accounts. The impl must be an
  **explicit, per-row** choice.
- So the right shape is the established **impl-class** pattern (`storage.Backend.
  backend_class`): one concrete domain model (one table) whose row names its
  implementation class. Generalize that into a typed, reusable base field.

## The rule to establish (and write into `docs/backend/guidelines.md`)

Codify *when each tool applies*, because the integration-lift previously
"dissolved the generic Provider `impl_class` FQN string — behavior on the owning
model" (see `CHANGELOG.md` / the integration-lift work). That is not a
contradiction; the boundary is:

- **Different persisted fields per variant → a model subclass** (`Capability`/
  `Bridge` subclasses; behavior + data on the model).
- **Same fields, only behavior differs per variant → one concrete model + an
  `ImplClassField` naming a non-model strategy/client class** (e.g.
  `VCSIntegration` row → a `GitHubClient`; `InferenceIntegration` row → an
  `AnthropicClient`). This keeps one table (unified list/reconcile, no field
  duplication) while behavior stays explicit and per-row.

Make this distinction explicit in the backend guideline so future addons pick
correctly instead of re-litigating it.

## Inventory to analyze and classify (file:line)

Walk the whole platform + addons; the known sites are below. For each, classify as
**(a) impl-class-per-row → migrate to `ImplClassField`**, **(b) closed-kind →
handler registry → leave as-is or reconcile**, or **(c) model-subclass discovery →
leave as-is**, and justify.

- `addons/angee/storage/models.py:111` — `Backend.backend_class = CharField(200)`;
  resolved `:162` (`import_string`), validated `issubclass(StorageBackend)`,
  cached resolved instance (`storage` property, `:152`), with
  `resolved_config()` env-placeholder expansion (`:133`). **The canonical (a).**
  The migration must preserve the cache + config-resolution behavior (decide
  whether those belong on the field, a descriptor, or stay on the model).
- `addons/angee/integrate/models.py:172` — `Integration.impl_class = CharField(200,
  blank=True)`; resolved `:210` `import_string(self.impl_class)(self)`. **(a)**,
  added during the in-flight VCS work; the first real consumer of the unified field.
- `addons/angee/iam/credentials.py` — `CredentialKind` (closed `TextChoices`) +
  `register_handler`/`handler_for` registry (`:59`,`:68`,`:168-170`);
  `Credential.handler` (`models.py:811`). **Likely (b)** — finite kinds, not
  arbitrary per-row classes. Decide whether it should stay an enum→handler registry
  or whether an open `ImplClassField` is warranted (probably keep; document why).
- `addons/angee/integrate/registry.py` — `capability_models`/`bridge_models`/
  `source_models` (`issubclass` scans over `apps.get_models()`). **(c)** — model
  discovery, not a field; leave, but note its relationship to the resolver.
- `angee/graphql/schema.py:395` — `import_string(dotted_path)`; check what it
  resolves (schema parts) and whether it shares the concern.
- The in-flight VCS code (`addons/angee/integrate/vcs/host.py` `GitHost` ABC,
  `addons/angee/integrate_github/host.py` `GitHubHost`) — these are the impl
  classes the new field will reference; they will be reshaped onto the final
  pattern. Treat as context, not blockers.

Also grep for any other `*_class = models.CharField`, `import_string`, `register_*
/ *_for(` strategy/handler/backend patterns you find beyond this list.

## Research first (don't reinvent)

The user explicitly asked: does Django or a library already provide this? Evaluate
and report before designing:

- **Django built-ins** — there is no first-class "reference to a Python class"
  field; the idiom is `CharField` + `django.utils.module_loading.import_string`.
  Confirm and cite.
- **`django-typed-models`** — single-table typed models (a `type` column resolves a
  row to its Python *model* subclass). Closest off-the-shelf option for the
  *model-polymorphism* case. Evaluate, but note it targets model subclasses, not
  non-model strategy classes — likely the wrong tool here (we are deliberately
  avoiding per-provider models), but document the comparison.
- **`django-polymorphic`** — multi-table model polymorphism; fragments the table
  (the thing we are avoiding). Document why it's rejected.
- Anything else (`django-model-utils`, `django-extensions`, a maintained
  "class/path field") — quick scan, cite findings.

If nothing fits the "row → non-model impl class" need cleanly, build
`ImplClassField` in `angee/base/fields.py` (joining `SqidField`/`StateField`/
`EncryptedField`, which each wrap a concern as a base field).

## Design requirements for `ImplClassField`

- **Typed to a base class.** `ImplClassField(base_class=StorageBackend, …)` — the
  field knows the expected base so it can validate `issubclass` on save and in a
  Django **system check** (`E`-code), failing fast on an unknown/invalid/abstract
  target. Mirror how `StateField(choices_enum=…)` is parameterized.
- **Dotted-path vs registry-key — decide deliberately.** A free-form FQN stored in
  a writable DB row is a code-execution surface (`import_string` of attacker-set
  text). Strongly consider a **registry/allowlist**: impls register themselves
  (like credential handlers / `bridge_models()` discovery) and the field stores a
  short key validated against the registry, resolving to the class. Weigh
  registry-key (safe, addon-registered, discoverable for a picker) vs raw dotted
  path (flexible, unsafe). Recommend one; this is the central design call.
- **Resolution + caching.** Provide a clean resolver (`field` descriptor or a model
  helper) returning the class, and optionally a cached bound instance — without
  re-implementing storage's `(pk, frozen config)` cache at every call site. Decide
  where the cache lives.
- **Construction contract.** Most call sites resolve then instantiate bound to the
  owning row (`import_string(self.backend_class)(...)`, `import_string(self.
  impl_class)(self)`). Define a consistent constructor contract (what the impl
  receives — the model instance? config?).
- **Composer/runtime emission.** It is a normal model field, so it must emit
  cleanly through the composer (`angee/compose/runtime.py`) like the other base
  fields; verify deterministic, drift-free emission and migrations.
- **GraphQL boundary.** Decide how it projects (a key/string scalar + a choices
  query for pickers, gated to admin). Look at how `StateField`/`SqidField` cross
  the boundary.
- **Migration of existing columns.** `backend_class` and `impl_class` are live
  `CharField`s; plan zero-loss migration (same DB representation if it stays a
  string, or a data migration if moving to registry keys).

## Constraints (from the architect — do not violate)

- The impl is an **explicit per-row** choice. **Never derive it from the vendor
  slug** (multiple impls/accounts per vendor). Vendor stays a pure catalogue.
- One field, **DRY**, at the base level; do not re-hand-roll per addon.
- Keep behavior **on the owning model** for the domain method; the impl class owns
  only the variant-specific behavior (transport/wire format).

## Deliverables & where they go (per the repo knowledge policy)

1. **Analysis + design doc** → `.agents/plans/impl-class-field.md` (or extend this
   handover): the inventory classification, the research findings (Django/library),
   the recommended design (registry-key vs dotted-path), and a per-call-site
   migration plan.
2. **The convention/rule** ("model subclass vs `ImplClassField`", and the
   security/registry stance) → `docs/backend/guidelines.md` (and its Pitfalls).
   Durable conventions live in `docs/`, not in private memory.
3. **Implementation** (gated on review): `angee/base/fields.py` `ImplClassField`
   (+ any registry helper in `angee/base/`), then adopt it in `storage.Backend`
   and `integrate.Integration`, deleting the hand-rolled resolution.

## Verification

- `uv run python -m pytest` (storage + integrate suites at minimum), `-m mypy`,
  `-m ruff` over `angee/base`, `addons/angee/storage`, `addons/angee/integrate`.
- `uv run examples/notes-angee/manage.py angee build && … makemigrations &&
  … migrate && … check` — confirm clean emission, migration, and the new system
  check firing on a bad impl reference.
- Round-trip: a `Backend`/`Integration` row resolves its impl through the new field
  exactly as before (resolved class identity + cache + config behavior preserved).
