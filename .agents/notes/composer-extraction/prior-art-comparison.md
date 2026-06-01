# Composer prior-art comparison — does "build-time Django composer" already exist?

**Date:** 2026-05-31
**Context:** Evaluating whether Angee's `compose` element could be extracted and open-sourced as a
generic, reusable Django plugin/composition package. This note records a mechanism-level comparison
of every adjacent package found, so we stop re-discovering the landscape.

## The one-sentence finding

The combination Angee implements — **deterministic build-time EMISSION of generated Django source
from declaratively-composed addon contracts** — is **not filled by any packaged tool**. The field
splits cleanly into two camps that never overlap:

- **Camp A — plugin composition, but RUNTIME (emits nothing):** DJP, Open edX plugins, django-oscar,
  Wagtail hooks, django CMS apphooks, pluggy, stevedore, entry points, django-plugins, pluginlib.
- **Camp B — build-time emission, but NO composition (single spec/app scaffold):** django-make-app,
  datamodel-code-generator, drf-generators, swagger-django-generator, django-api-generator (AppSeed),
  django-code-generator.

Nobody sits in both. Angee is the empty cell.

## What Angee's composer does (baseline)

Build-time, emit-only. Discovers Django `AppConfig`s subclassing `BaseAddonConfig` from
`INSTALLED_APPS`; topo-orders by `depends_on` (deterministic, name tie-break); reads each addon's
contract (abstract models, model `extends=` extensions merged via MRO, GraphQL `schema.py` fragments,
REBAC `.zed` permissions, resource manifests, settings defaults); EMITS generated Python source to a
`runtime/` dir — concrete Django models (abstract→concrete, extension bases composed into the
inheritance chain, **field-collision detection**), GraphQL SDL, permissions — guarded by a
`# ANGEE GENERATED RUNTIME - DO NOT EDIT` sentinel, preserving `migrations/`; fully deterministic;
re-emits if stale on boot; **no runtime monkey-patching**.

The clean seam: everything above `AngeeRuntime.render_sources() -> dict[Path, str]`
(`src/angee/compose/runtime.py:75`) is generic composition machinery; everything inside it is
Angee-specific emission.

## Master comparison

Axes: **Timing** (Runtime / Build-time), **Emits source?**, **Composes multiple addons?**,
**Deterministic order?**, **Touches ORM models?**, **Match** = closeness to "build-time emit +
generic composition" (0–5).

| Package | Timing | Emits source | Composes addons | Determ. order | ORM models | Match |
|---|---|---|---|---|---|---|
| **Angee (us)** | Build | ✅ models+SDL+perms→`runtime/` | ✅ many→one MRO | ✅ topo + name | ✅ abstract→concrete + merge | — |
| DJP (Willison, on pluggy) | Runtime | ❌ | ✅ apps/mw/urls/settings | ~ anchors (Before/After/Position) | ❌ (rides INSTALLED_APPS) | 1 |
| Open edX plugins (edx-django-utils) | Runtime | ❌ | ✅ apps/urls/settings/signals | ❌ (entry-point order) | ❌ | 2 |
| django-oscar | Runtime (+1-shot fork) | ⚠️ `oscar_fork_app` one-shot scaffold | ❌ project overrides core | ❌ import order | ✅ AbstractX→X (hand-written) | 2 |
| Wagtail hooks | Runtime | ❌ | ✅ behavior/UI hooks | ✅ `order=±N` stable sort | ❌ | 2 |
| django CMS apphooks | Runtime | ❌ | ~ URL mounts per page | ~ alpha (display only) | ❌ | 1 |
| pluggy | Runtime | ❌ | ✅ 1:N hook dispatch | ✅ LIFO + tryfirst/trylast/wrapper | ❌ (agnostic) | 2 |
| stevedore | Runtime | ❌ | ✅ named extension managers | ~ name_order opt-in | ❌ | 1 |
| importlib.metadata entry points | Decl=build / load=runtime | ❌ | ❌ (discovery only) | ❌ undefined | ❌ | 1 |
| django-plugins (krischer) | Runtime (DB/admin) | ❌ | ~ DB registry | ~ manual `index` | stores plugin rows | 1 |
| pluginlib | Runtime | ❌ | ✅ class registry | ❌ (version-wins) | ❌ | 1 |
| django-simple-plugins | Runtime (admin pipeline) | ❌ | ~ chain | manual (admin) | config rows | 0 |
| django-app-plugins | Runtime (templates) | ❌ | ~ template concat | INSTALLED_APPS | ❌ | 0 |
| django-make-app | Build (1-shot) | ✅ full app tree from YAML | ❌ single spec | ❌ | ✅ emits models | 1 |
| django-code-generator (Nekmo) | Build (1-shot) | ✅ from existing models | ❌ | ❌ | reads, not emits | 0 |
| swagger-django-generator | Build (1-shot) | ✅ views/stubs (no models) | ❌ single spec | ❌ | ❌ | 0 |
| drf-generators | Build (1-shot) | ✅ DRF glue from models | ❌ | ❌ (`--force` clobber) | reads, not emits | 0 |
| django-api-generator (AppSeed) | Build (**idempotent re-emit**) | ✅ `api/` overwritten each run | ~ flat settings registry | ~ whole-dir clobber | reads, not emits | 2 |
| datamodel-code-generator | Build | ✅ Pydantic/dataclass (NOT Django) | ❌ single spec | ~ input-order stable | ❌ wrong target | 2 |
| Strawberry-Django | Runtime (+SDL export) | ⚠️ SDL via `export_schema` | ✅ schema merge (`merge_types`/inherit) | ~ def order | exposes, not emits | 3 |
| Graphene-Django | Runtime (+SDL export) | ⚠️ SDL via `graphql_schema` | ✅ schema merge (inherit) | ✅ **sorted canonical SDL** | exposes, not emits | 3 |
| cookiecutter-django | Init (1-shot) | ✅ project skeleton once | ❌ template flags | ❌ | ❌ | 1 |
| Pinax | Runtime (prebuilt apps) | ❌ (+cli scaffold) | ❌ bundled apps | ❌ | hand-written | 1 |
| Nx / Pants | Per-build task cache | ⚠️ protobuf→stub (ephemeral) | ❌ orchestrates existing pkgs | ✅ hash/affected | ❌ | 1 |
| Turborepo / Bazel | Per-build task cache | ❌ / hermetic stubs | ❌ | ✅ | ❌ | 0 |

Highest match is Strawberry/Graphene-Django (3/5) — but only on the **SDL-composition** sub-axis, and
they're runtime + introspect hand-written models rather than emit them. No tool exceeds 3.

## The reference points worth studying (per axis)

- **Contract + discovery ergonomics → DJP** (and the edX `plugin_app` dict). DJP's typed hook contract
  (`installed_apps()/middleware()/urlpatterns()/settings()/asgi_wrapper()`) over **entry-point
  discovery** is the cleanest model for "addons announce themselves across packages without editing
  `INSTALLED_APPS`." edX's `{config_type: {target: {...}}}` declarative dict is the battle-tested
  production shape.
- **Ordering primitives → pluggy + DJP.** pluggy's `tryfirst/trylast/wrapper` (ordering *within* a
  phase) and DJP's `Before/After/Position` anchors (inject into a *foreign* sequence you don't own)
  both complement Angee's coarse `depends_on` topo-sort.
- **Idempotent managed-dir re-emit → django-api-generator (AppSeed).** The only Camp-B tool on Angee's
  re-emit axis: regenerates a managed `api/` dir every run from a settings-level `SLUG→import_path`
  registry. Validates Angee's `runtime/` model; Angee already hardens it with a sentinel + sorting it
  lacks.
- **Determinism bar → graphene-django.** The only tool with a *documented* "sorted canonical SDL"
  guarantee. That's the bar Angee's emitted SDL should meet or beat.
- **Abstract→concrete model precedent → django-oscar.** `AbstractProduct`→`Product` is the closest ORM
  analog, but it's a single hand-written subclass owned by the project — no multi-addon merge, no
  collision detection. Angee's `extends=` MRO-merge with field-collision detection is strictly more.
- **Robust codegen internals → datamodel-code-generator.** Mature, widely-used emitter handling
  `$ref/allOf/oneOf`/enums/nesting with selectable output templates — a reference for emission
  robustness even though it targets Pydantic, not Django.

## Concrete things Angee could borrow (mapped to gaps)

1. **Entry-point discovery** (importlib.metadata, as DJP/edX/stevedore use) — lets third-party addons
   register across packages without each consumer hand-editing `INSTALLED_APPS`. Today Angee discovers
   only `BaseAddonConfig`s already in `INSTALLED_APPS`. This is the single biggest gap for a *public*
   package: an open-source composer probably can't assume consumers list every addon by hand.
2. **`tryfirst/trylast` (pluggy) + `Before/After/Position` (DJP)** — finer ordering than `depends_on`
   for contributions into sequences an addon doesn't own.
3. **graphene-django's canonical-sorted SDL** — adopt/verify as Angee's determinism guarantee for SDL.
4. **AppSeed's settings-level greppable registry** — a public, auditable enumeration of "what gets
   emitted," complementing the implicit AppConfig scan.
5. **CMS's "explicit list disables autodiscovery"** — a deterministic, pin-exactly-what-composes mode.
6. **pluginlib's abstractmethod contract enforcement** — fail-fast on a malformed addon contract
   (Angee should do this at **build** time, not import).

## Implication for the open-source decision

The niche is genuinely open — extracting Angee's composition core would occupy an **empty cell**, not
duplicate prior art. Two honest caveats:

- "Open niche" can mean *novel* **or** *no demand*. The runtime-registry camp is crowded and mature
  (DJP, edX) precisely because most teams want runtime composition; the build-time-emit appetite is
  unproven outside Angee. Worth a deliberate "who else wants this" gut-check before investing in
  packaging.
- The two reference designs to position against when packaging: **DJP** (contract/entry-point model,
  minimal surface) and **edx-django-utils** (production-grade multi-app composition). Borrow their
  discovery/contract shape; keep Angee's emit core, which neither has.

The extraction is the same regardless: keep the generic half (discover / order / drift / emit / clean
/ sentinel + an **Emitter protocol**) and push Angee's emitters (model codegen, REBAC, history, SDL)
behind that protocol as the framework's own plugins.
