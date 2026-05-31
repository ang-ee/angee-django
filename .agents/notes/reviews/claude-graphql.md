# Focused Review — decompose & optimize `base/graphql` (Claude)

### Summary

The `base/graphql` package conflates three layers: a **runtime serving** concern
(build the live schema, serve it over HTTP/WS), a **build-time emission** concern
(`render_sdl` printing SDL files for drift review), and a **REBAC policy** concern
buried inside `subscriptions.py`. The single highest-value change is to split the
schema concern around the seam that already exists: a `GraphQLSchemas` registry
owner in `base.graphql` builds live schemas from discovered addons, while SDL
rendering/emission/drift moves up to `angee.compose` as a thin caller of that
owner — and `subscriptions.py` sheds its read-gating policy to the REBAC layer so
graphql owns only the Strawberry surface, not the authorization rules.

### Placement findings (build vs runtime)

| Responsibility | Lives now | Should live | Seam |
|---|---|---|---|
| `collect_schema_parts` / `collect_schema_names` | `schema.py:37`, `schema.py:54` | `base.graphql` (runtime) — methods on a `GraphQLSchemas` owner | reads `AppConfig.schema_parts`; pure registry fold over discovered addons |
| `build_schema` | `schema.py:62` | `base.graphql` (runtime) — `GraphQLSchemas.build(name)` | consumed on the serving path by `views.py:19`, `asgi.py:21` |
| `_merge_root` / `_dedupe_by_identity` | `schema.py:106`, `schema.py:128` | `base.graphql` (runtime) — private methods of the owner | merge + fail-fast collision; needs `introspection.surface_field_names` |
| `render_sdl` (print SDL to `{name: str}`) | `schema.py:94` | **`angee.compose`** (build-time) | the *only* build-time function in the package; called solely by `compose/emission.py:20,153,161` (`emit_schema_sdl`/`check_schema_sdl`), never on the serving path |
| `ChangeEvent` GraphQL type | `subscriptions.py:35` | `base.graphql` (runtime) — stays | Strawberry type |
| `changes()` factory | `subscriptions.py:46` | `base.graphql` (runtime) — stays | Strawberry subscription surface factory |
| channel subscribe stream `_subscribe` | `subscriptions.py:76` | `base.graphql` (runtime) but as its own module/class | reads `signals.change_group`; drains a channels group |
| REBAC gating `_gate_event` / `_redact` | `subscriptions.py:100`, `subscriptions.py:118` | **a REBAC read-policy object, outside graphql** (`base.signals` neighbor or a `rebac`-owned helper) | it switches on `model_resource_type(model)` and calls `check_field_access`/`gated_read_fields` — pure REBAC policy, no Strawberry in it |
| actor resolution `scope_actor` | `subscriptions.py:173` | **`base.consumers`** (its only caller) or a `base` auth module | resolves a channels-scope actor; imported by `consumers.py:14`; not a graphql fact |
| `_actor_from_info` | `subscriptions.py:180` | `base.graphql` (runtime) — reads the actor off the GraphQL context | belongs with the subscription resolver |

The clean seam for suspicion (1): **everything that builds or runs a live schema
stays in `base.graphql`; `render_sdl` is the lone build-time function and moves to
`compose`.** `render_sdl` is just `{name: build_schema(name).as_str()}`
(`schema.py:99-103`) — pure printing of the runtime owner's output. The layering
rule allows this directly: `compose` may import `base` (guidelines.md:52-53), so
`compose` can call `GraphQLSchemas.from_addons(addons).render_sdl()` without `base`
ever importing `compose`. Keeping `render_sdl` in `base.graphql` is not a layering
violation, but it is a *placement* smell — it puts a build-only emitter in the
runtime package, and the refactor plan (§1.3, §2.1) explicitly wants SDL rendered
in the build/run-settings step owned by `compose`. Move it; `base.graphql` should
expose only the live `build`/`names` surface the emitter calls.

### Proposed `base/graphql` (and neighbors) layout

```
base/graphql/
  __init__.py        re-export facade (__all__ allowed): crud, changes,
                     ChangeEvent, GraphQLSchemas
  introspection.py   surface_name, surface_field_names, django_model
                     (unchanged — sole reader of Strawberry internals)
  schema.py          class GraphQLSchemas — the registry owner
                       .from_addons(addons=None) -> GraphQLSchemas   (classmethod)
                       .names() -> tuple[str, ...]
                       .parts() -> dict[str, SchemaParts]            (cached fold)
                       .build(name=DEFAULT_SCHEMA_NAME) -> strawberry.Schema
                       ._merge_root(name, key, surfaces)             (private)
                       ._dedupe_by_identity(values)                  (private)
                     DEFAULT_SCHEMA_NAME constant
  crud.py            crud(...) factory + DeletePreview/DeletePreviewGroup
                     (delete-preview model logic moves out — see Findings #4)
  events.py          ChangeEvent type + the subscription resolver wiring;
  subscriptions.py   changes(model, field=...) factory + _actor_from_info +
                     the channel stream (ChangeStream below)
```

What moves **to `compose`**:
- `render_sdl` → `compose` (e.g. `compose/runtime.py` `AngeeRuntime.render_sdl()`
  or a `compose` schema-emit helper) — calls `GraphQLSchemas.from_addons(addons)
  .render_sdl()` and writes files. This is exactly the §2.4 `AngeeRuntime` lifecycle.

What `subscriptions.py` **splits into**:
- **`ChangeEvent`** (stays a Strawberry **class**) + `_to_event` builder.
- **`changes()`** — stays a **function** (a Strawberry surface factory; it builds a
  dynamic `type(...)`, has no instance state to own).
- **`ChangeStream`** (new **class**, or stays a `_subscribe` async generator
  function — borderline; a function is fine since it holds no cross-call state) —
  the channels group drain.
- **REBAC read-policy** (`_gate_event`/`_redact`) — moves **out of graphql** into a
  REBAC-owned read-gate. This is the `find the owner` smell: a function that takes a
  model + payload and *inspects* `model_resource_type` to decide visibility is REBAC
  policy, not GraphQL. Best home: a small policy object/function next to the
  publishers (it is the read-side mirror of `base.signals`' write-side publishing)
  or, ideally, contributed by the `rebac` library it already leans on
  (`rebac.field_visibility`). At minimum it leaves `subscriptions.py`.
- **`scope_actor`** — moves to **`base.consumers`** (its only caller, `consumers.py:14`).
- **`_actor_from_info`** — stays with the subscription resolver (it reads the
  GraphQL context).

### Findings

**1. (High) `render_sdl` is build-time code shipped in the runtime package.**
- **Location:** `schema.py:94-103`; consumers `compose/emission.py:20,153,161`.
- **Problem:** `render_sdl` prints SDL to files for drift review — a build/emit
  concern (plan §2.1, §2.4). It sits in the runtime serving package next to
  `build_schema`. No serving path calls it (the grep shows only `compose/emission.py`
  uses it; `views.py`/`asgi.py` use only `build_schema`/`collect_schema_names`). It
  inflates the runtime surface and blurs the build/runtime line the plan draws.
- **Recommendation:** Delete `render_sdl` from `base.graphql`; give the live-schema
  owner a `render_sdl()` *output* method (or let `compose` call `build(name).as_str()`
  per name) and own the file-writing in `compose`. `base.graphql` exposes only
  `GraphQLSchemas.{names,build}`.

**2. (High) `subscriptions.py` owns REBAC read-policy that is not a GraphQL fact.**
- **Location:** `subscriptions.py:100-155` (`_gate_event`, `_redact`),
  imports at `subscriptions.py:25-29`.
- **Problem:** Five of the file's imports are REBAC (`ObjectRef`, `SubjectRef`,
  `anonymous_actor`, `backend`, `check_field_access`, `gated_read_fields`,
  `model_resource_type`). `_gate_event` is the canonical "function that takes an
  object and inspects it to decide something" smell (AGENTS.md): it branches on
  `model_resource_type(model)` then decides read access and field redaction. This is
  REBAC's read-gate, identical in spirit to what middleware does for HTTP — it just
  happens to run at subscription emit. It does not belong to the GraphQL surface.
- **Recommendation:** Extract a read-gate owned by REBAC (or a `base` neighbor of
  `signals.py`, since it mirrors the write-side publisher). `subscriptions.py` then
  calls `gate.visible_event(model, actor, payload) -> ChangeEvent | None` and stays
  graphql-only. This also makes the gate reusable for any future read transport.

**3. (Medium) `subscriptions.py` bundles five responsibilities in one module.**
- **Location:** whole file `subscriptions.py:1-190`.
- **Problem:** Event type, surface factory, channel stream, REBAC gating, and actor
  resolution co-exist. The module docstring even has to enumerate all five. After
  #2 removes the gate and `scope_actor` moves to `consumers`, what remains
  (`ChangeEvent`, `changes`, `_subscribe`, `_actor_from_info`, `_to_event`) is one
  cohesive concern: "stream model changes as a Strawberry subscription."
- **Recommendation:** Keep the residual in one `subscriptions.py` (it is now
  cohesive) or split `ChangeEvent`+`_to_event` into `events.py` if it grows. Do not
  over-class it: `changes()` is a legitimate factory function, and `_subscribe` is a
  stateless async generator — neither earns a class (compose-onto-classes only when
  several functions share one object's state).

**4. (Medium) Cascade delete-preview in `crud.py` is a model/manager concern, not GraphQL.**
- **Location:** `crud.py:108-182` (`collect_delete_preview`, `_groups`,
  `_count_by_model`, `_resolve_for_delete`).
- **Problem:** `collect_delete_preview(instance)` drives Django's `Collector` to
  forecast a cascade — pure ORM behavior on an instance, with no Strawberry in it
  (`DeletePreview`/`DeletePreviewGroup` are the only Strawberry parts and they are
  just the output shape). Per the Django-Native Rule and "instance behavior lives on
  model methods" (guidelines.md:166-169), the forecast belongs on the model or its
  manager. The graphql module should *render* the preview, not *compute* it.
- **Recommendation:** Move the cascade forecast to the model/manager layer
  (e.g. `AngeeModel.delete_preview()` returning a plain dataclass/value, or a manager
  method), and have `crud.py`'s `_delete_resolver` call it and adapt the value into
  the `DeletePreview` Strawberry type. The Strawberry `DeletePreview`/`...Group`
  types stay in graphql (they are the API surface); the `Collector` logic does not.
  This also removes `crud.py`'s dependency on `django.db.models.deletion` internals
  (`crud.py:20`).

**5. (Low) `schema_parts` merge is a missing `GraphQLSchemas` owner class.**
- **Location:** `schema.py:37-139` — `collect_schema_parts`, `collect_schema_names`,
  `build_schema`, `render_sdl`, `_merge_root`, `_dedupe_by_identity` are five+ loose
  functions all threading the same `addons`/`collected` state.
- **Problem:** This is the compose-onto-classes case (guidelines.md:96-106): several
  functions take the same discovered-addon set and read/fold/emit from it. The
  collected `dict[str, SchemaParts]` is computed twice (`collect_schema_names` →
  `collect_schema_parts`, then `build_schema` → `collect_schema_parts` again, and
  `render_sdl` calls both), so each `build_application()`/serving call re-discovers
  and re-folds. A `GraphQLSchemas` owner would fold once and cache.
- **Recommendation:** Introduce `GraphQLSchemas` (plan §2.4 flags this as optional;
  it is warranted): `from_addons(addons=None)` discovers once, `parts()` is the
  cached fold, `names()`/`build(name)` read it. `_merge_root`/`_dedupe_by_identity`
  become private methods. The plan's "only if it reads cleaner" gate is met — the
  double-fold and the shared-state thread are real duplication.

**6. (Low) `model_resource_type` is a build-import / runtime-import duplication risk; gating runs per event.**
- **Location:** `subscriptions.py:105`, `subscriptions.py:127` call
  `model_resource_type(model)` and `gated_read_fields(model)` on **every** event.
- **Problem:** Both are functions of `model` alone, invariant across a subscription's
  lifetime, but recomputed per payload. Minor, but it is policy lookup on a hot path.
- **Recommendation:** When the read-gate moves out (#2), resolve `resource_type` and
  `gated` fields once per subscription (the gate object is built per `changes()`
  surface / per stream) rather than per event.

**7. (Low) Dead/unearned surface in the `__init__` facade and naming nits.**
- **Location:** `__init__.py:8-27`; `subscriptions.py` actor helpers.
- **Problem:** `render_sdl` is re-exported as public API (`__init__.py:14,26`) yet
  has exactly one internal consumer in `compose`; after #1 it should not be part of
  the runtime facade. `collect_schema_parts`/`collect_schema_names` are also
  re-exported but only `collect_schema_names` is used externally (`asgi.py:33`);
  `collect_schema_parts` has no external caller and should be a private fold under
  the owner (#5). `scope_actor` (`subscriptions.py:173`) reads as a verb-first
  accessor but is mis-homed (it is in graphql, used by consumers) — rename/move to
  `consumers` as the actor it resolves. No truly dead code, but the public facade is
  wider than the real contract.
- **Recommendation:** Trim the `__init__` facade to the genuine public API
  (`crud`, `changes`, `ChangeEvent`, `GraphQLSchemas`, `DEFAULT_SCHEMA_NAME`); drop
  `render_sdl`, `collect_schema_parts`, and the standalone `build_schema`/
  `collect_schema_names` functions in favor of the owner's methods (serving code
  calls `GraphQLSchemas.from_addons().build(name)` / `.names()`).

**Layering check:** Nothing in `base.graphql` imports `angee.compose` today — the
edge runs the correct direction (`compose/emission.py` imports `base.graphql`), so
the `base ↛ compose` rule (guidelines.md:52) holds. The placement issue is not a
*violation* but a *misallocation*: build-only `render_sdl` sitting in runtime.
Moving it to `compose` keeps the dependency one-way and matches the plan's
build/runtime split.

### Top recommendations

1. Move `render_sdl` to `angee.compose` (call `GraphQLSchemas.build(name).as_str()`);
   leave only live `build`/`names` in `base.graphql`.
2. Extract the REBAC read-gate (`_gate_event`/`_redact`) out of `subscriptions.py`
   into a REBAC-owned policy object — graphql must not decide authorization.
3. Introduce a `GraphQLSchemas` owner class to fold parts once and own
   `build`/`names`/`render`, replacing the five loose `schema.py` functions and the
   double fold.
4. Move cascade `collect_delete_preview` to the model/manager layer; `crud.py`
   renders the preview type, it does not compute it with `Collector`.
5. Relocate `scope_actor` to `base.consumers` and trim the `__init__` facade to the
   real public contract.
