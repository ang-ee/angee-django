### Summary
`base/graphql` has two different concerns mixed together: live runtime schema serving and generated SDL artifacts for build drift review. The highest-value change is to introduce a small runtime `GraphQLSchemas` owner in `base.graphql` and move all SDL file rendering/checking to `compose`, with `compose` importing `base` and never the reverse. `subscriptions.py` should then be split by owner: GraphQL event/type surface, channel stream, REBAC gate, and actor resolution.

### Placement findings (build vs runtime)
- **Addon `schemas` declaration normalization**: now `BaseAddonConfig.schema_parts` reads and normalizes `graphql.py.schemas` in `src/angee/base/apps.py:290`; it should stay in `base.apps` because `AppConfig` owns addon-local declaration facts per `docs/backend/guidelines.md:153`. Seam: GraphQL merge code asks `addon.schema_parts`, never re-scans addon modules.

- **Schema part collection**: now `collect_schema_parts()` folds addon parts in `src/angee/base/graphql/schema.py:37` and discovers addons through `angee.base.discovery` at `src/angee/base/graphql/schema.py:42`. It should stay in `base.graphql` because serving code also enumerates schemas, and discovery is explicitly a runtime registry read in `docs/backend/guidelines.md:57` and the plan’s R1 at `.agents/plans/2026-05-30-compose-base-resources-refactor.md:8`. Seam: `GraphQLSchemas.from_discovery()` for serving, `GraphQLSchemas.from_addons(addons)` for compose/test callers.

- **Schema name collection**: now `collect_schema_names()` is in `src/angee/base/graphql/schema.py:54` and is used by ASGI routing in `src/angee/base/asgi.py:33`. It should stay in `base.graphql`; it is runtime routing input, not build output. Seam: `asgi.py` calls `GraphQLSchemas.names()` once when building websocket routes.

- **Live schema building**: now `build_schema()` is in `src/angee/base/graphql/schema.py:62`, used by HTTP views at `src/angee/base/views.py:19`/`src/angee/base/views.py:27` and WebSocket routes at `src/angee/base/asgi.py:31`. It should stay in `base.graphql`. Seam: return a live `strawberry.Schema`; no file paths, runtime directory, drift checks, or compose imports.

- **Root merge and collision detection**: now `_merge_root()` and `_dedupe_by_identity()` live as loose helpers in `src/angee/base/graphql/schema.py:106` and `src/angee/base/graphql/schema.py:128`. They should become private methods on `GraphQLSchemas`, because several functions share the same collected schema state and the guidelines prefer composing behavior onto the owner at `docs/backend/guidelines.md:96`. Seam: `GraphQLSchemas.build(name)` owns merge/collision behavior; `introspection.surface_field_names()` remains the Strawberry-internal reader.

- **SDL rendering and drift output**: now `render_sdl()` is exported from runtime `base.graphql` at `src/angee/base/graphql/schema.py:94` and `src/angee/base/graphql/__init__.py:9`, then compose writes/checks `runtime/schemas/*.graphql` at `src/angee/base/compose/emission.py:144` and `src/angee/base/compose/emission.py:157`. This should move to `angee.compose`, preferably `AngeeRuntime.render_schema_sdl()` / `write_schema_sdl()` / `check_schema_sdl()`. Seam: in the fresh run-settings process required by R3 (`.agents/plans/2026-05-30-compose-base-resources-refactor.md:31`), compose imports `GraphQLSchemas.from_addons(addons)`, calls `build(name).as_str()`, and owns paths/diffs under `runtime/schemas/`.

- **CRUD mutation factory**: now `crud()` is in `src/angee/base/graphql/crud.py:45` and delegates create/update to `strawberry_django.mutations` at `src/angee/base/graphql/crud.py:72`. It should stay in `base.graphql`; `docs/stack.md:30` says strawberry-django owns GraphQL types/resolvers and Angee adds only shortcuts. Seam: addon `graphql.py` calls `crud()` as in `examples/notes-angee/src/example/notes/graphql.py:85`.

- **Delete cascade preview**: now `collect_delete_preview()` and `DeletePreview` are in GraphQL code at `src/angee/base/graphql/crud.py:34` and `src/angee/base/graphql/crud.py:108`. The cascade computation should move outside GraphQL to a model/manager-adjacent owner, e.g. `base/deletion.py` with `DeletionPreview.from_instance(instance)`, because Django model behavior belongs on models/managers/querysets per `docs/backend/guidelines.md:85`. Seam: GraphQL keeps Strawberry output types/adapters and calls the deletion owner from the resolver.

- **Strawberry internal readers**: now centralized in `src/angee/base/graphql/introspection.py:14`, `src/angee/base/graphql/introspection.py:20`, and `src/angee/base/graphql/introspection.py:31`. They should stay in `base.graphql.introspection`; this is the correct shared seam between `crud` and schema merge. Seam: only this module touches `__strawberry_definition__` and `__strawberry_django_definition__`.

- **Change event GraphQL type**: now `ChangeEvent` lives in overloaded `src/angee/base/graphql/subscriptions.py:35`. It should move to `base.graphql.events` or `base.graphql.types`. Seam: `ChangeEvent.from_payload(payload)` converts the shared payload into GraphQL shape.

- **Subscription surface factory**: now `changes()` creates the Strawberry subscription and wires publishers in `src/angee/base/graphql/subscriptions.py:46`. It should remain the public `base.graphql` API, but delegate to a `ModelChangeSubscription` class with `as_type()` / `resolve()` methods. Seam: `changes(model, field=...)` stays a thin factory for addon authors.

- **Channel-layer subscribe stream**: now `_subscribe()` owns group join/receive/discard in `src/angee/base/graphql/subscriptions.py:76`. It should move to `base.graphql.streams.ChangeStream`, because channel subscription state is separate from GraphQL type construction and REBAC policy. Seam: `ChangeStream(model).payloads()` yields raw payloads from `base.signals.change_group(model)`.

- **REBAC read gate and field redaction**: now `_gate_event()`, `_redact()`, and `_to_event()` are bundled in `src/angee/base/graphql/subscriptions.py:100`, `src/angee/base/graphql/subscriptions.py:118`, and `src/angee/base/graphql/subscriptions.py:158`. They should move to `base.graphql.access.ChangeReadGate`, with `filter(payload) -> ChangeEvent | None`. Seam: it delegates actual permission checks to `django-zed-rebac`, whose ownership is declared in `docs/stack.md:34`.

- **Actor resolution**: now `scope_actor()` and `_actor_from_info()` live in subscriptions at `src/angee/base/graphql/subscriptions.py:173` and `src/angee/base/graphql/subscriptions.py:180`, while the consumer imports `scope_actor` from there at `src/angee/base/consumers.py:14`. Scope actor resolution should move outside `graphql.subscriptions`, e.g. `base.actors.resolve_scope_actor()`, because the WebSocket transport owns scope-to-context setup. Seam: `AngeeGraphQLWSConsumer.get_context()` attaches `actor`; GraphQL subscription code only reads `actor` from `info.context`.

- **Change publishers**: now correctly live in `base.signals`, with `change_group()` at `src/angee/base/signals.py:44`, `connect_publishers()` at `src/angee/base/signals.py:50`, and payload broadcasting at `src/angee/base/signals.py:88`. They should stay in `base.signals`. Seam: subscriptions import only `connect_publishers` and `change_group`; signal payload construction should use the existing public-id owner in `src/angee/base/models.py:108`.

### Proposed `base/graphql` (and neighbors) layout
- `base/graphql/__init__.py`: stable re-export facade for `crud`, `changes`, `ChangeEvent`, `build_schema`, `collect_schema_names`, and possibly `collect_schema_parts`; remove `render_sdl` from the runtime public API.

- `base/graphql/introspection.py`: keep `surface_name`, `surface_field_names`, and `django_model` as pure functions; this remains the only Strawberry-internal reader.

- `base/graphql/schema.py`: introduce `GraphQLSchemas`.
  - `GraphQLSchemas.from_discovery()`
  - `GraphQLSchemas.from_addons(addons)`
  - `parts`
  - `names()`
  - `build(name)`
  - private `_merge_root()`, `_dedupe_by_identity()`
  - compatibility facades: `collect_schema_parts()`, `collect_schema_names()`, `build_schema()`

- `base/graphql/crud.py`: keep `crud()` and the dynamic mutation surface construction. Move Collector-based preview computation out; the delete resolver calls the deletion owner and adapts the result to GraphQL.

- `base/graphql/delete.py`: optional GraphQL-only `DeletePreview` and `DeletePreviewGroup` Strawberry types plus `DeletePreview.from_domain(preview)`. If this feels too much for the current surface, keep the types in `crud.py` but not the Collector logic.

- `base/graphql/events.py`: `ChangeEvent` Strawberry type with `from_payload()`.

- `base/graphql/subscriptions.py`: public `changes()` facade and `ModelChangeSubscription`.
  - `ModelChangeSubscription.__init__(model, field)`
  - `ModelChangeSubscription.as_type()`
  - `ModelChangeSubscription.resolve(info)`
  - This class wires `connect_publishers(model)` but delegates stream, actor lookup, and gating.

- `base/graphql/streams.py`: `ChangeStream`.
  - `ChangeStream.__init__(model, layer=None)`
  - `ChangeStream.payloads()`
  - Owns `new_channel()`, `group_add()`, `receive()`, and `group_discard()`.

- `base/graphql/access.py`: `ChangeReadGate`.
  - `ChangeReadGate.__init__(model, actor, backend=None)`
  - `ChangeReadGate.filter(payload)`
  - private row-read and field-redaction methods.
  - Delegates to `model_resource_type`, `backend`, `check_field_access`, and `gated_read_fields`.

- `base/graphql/context.py`: `actor_from_info(info)` only, if that helper is still useful after the split.

- `base/actors.py`: `resolve_scope_actor(scope)` for Channels scope actor resolution. `base/consumers.py` imports this instead of importing from subscriptions.

- `base/signals.py`: keep `register_revision_models`, `change_group`, `connect_publishers`, signal handlers, and broadcasting. Use `public_id_of(instance)` instead of assuming `instance.public_id`.

- `base/deletion.py`: `DeletionPreview` / `DeletionPreviewGroup` domain objects and `DeletionPreview.from_instance(instance)`. This module owns Django `Collector` usage; GraphQL only serializes the result.

- `compose/graphql.py` or `compose/runtime.py`: move SDL artifact ownership here.
  - `render_schema_sdl(addons) -> dict[str, str]`
  - `write_schema_sdl(runtime_dir, addons)`
  - `check_schema_sdl(runtime_dir, addons)`
  - Or make these `AngeeRuntime` methods, consistent with `.agents/plans/2026-05-30-compose-base-resources-refactor.md:240`.

### Findings
1. **High: SDL artifact generation is exposed as runtime GraphQL API**  
   **Location**: `src/angee/base/graphql/schema.py:94`, `src/angee/base/graphql/__init__.py:9`, `src/angee/base/compose/emission.py:144`  
   **Problem**: `render_sdl()` is a generated-artifact concern, but it is exported beside runtime serving helpers. The authoritative plan says SDL is rendered in a separate run-settings build phase at `.agents/plans/2026-05-30-compose-base-resources-refactor.md:31`, while serving modules must not import compose per `docs/backend/guidelines.md:50`.  
   **Recommendation**: Move multi-schema SDL rendering/writing/checking into `compose`; keep `base.graphql` responsible for live schema names and `strawberry.Schema` construction only.

2. **High: `subscriptions.py` has too many owners in one file**  
   **Location**: `src/angee/base/graphql/subscriptions.py:35`, `src/angee/base/graphql/subscriptions.py:46`, `src/angee/base/graphql/subscriptions.py:76`, `src/angee/base/graphql/subscriptions.py:100`, `src/angee/base/graphql/subscriptions.py:173`  
   **Problem**: One module owns the event type, schema factory, channel-layer stream, REBAC gate, field redaction, and actor resolution. This violates the owner map rule in `AGENTS.md:66` and makes `base/consumers.py` depend on a subscription implementation detail at `src/angee/base/consumers.py:14`.  
   **Recommendation**: Split into `events`, `streams`, `access`, `context`, and a thin `subscriptions` facade; move scope actor resolution to a runtime neighbor outside GraphQL subscriptions.

3. **Medium: schema merge is missing a registry owner**  
   **Location**: `src/angee/base/graphql/schema.py:37`, `src/angee/base/graphql/schema.py:62`, `src/angee/base/graphql/schema.py:106`  
   **Problem**: Collection, name listing, root merging, and schema building are loose functions over the same addon/parts state. ASGI currently collects names and then rebuilds schemas separately at `src/angee/base/asgi.py:28`, which repeats discovery/collection work.  
   **Recommendation**: Add `GraphQLSchemas` as the owner with `names()` and `build(name)`; keep module-level facades only for compatibility.

4. **Medium: delete cascade preview is not a GraphQL concern**  
   **Location**: `src/angee/base/graphql/crud.py:108`, `src/angee/base/graphql/crud.py:137`, `src/angee/base/graphql/crud.py:152`  
   **Problem**: Django `Collector` cascade analysis and row deletion are model lifecycle behavior, but the logic lives inside a GraphQL shortcut. The resolver also performs its own public-id lookup instead of delegating deletion semantics to a model/manager-adjacent owner.  
   **Recommendation**: Move cascade preview to `base.deletion` or a model/manager method, and leave GraphQL to expose the mutation and serialize the preview.

5. **Medium: signal payload construction duplicates public-id ownership**  
   **Location**: `src/angee/base/signals.py:107`, `src/angee/base/signals.py:109`, `src/angee/base/models.py:108`  
   **Problem**: `changes()` accepts any `models.Model` at `src/angee/base/graphql/subscriptions.py:46`, but the publisher assumes `instance.public_id`. The repository already has `public_id_of(instance)` as the single owner for AngeeModel-or-plain-Django identity.  
   **Recommendation**: Use `public_id_of(instance)` in `base.signals` and consider a small shared payload value object so publisher and GraphQL event conversion do not duplicate the payload contract.

6. **Low: the current refactor plan still has stale GraphQL placement text**  
   **Location**: `.agents/plans/2026-05-30-compose-base-resources-refactor.md:139`, `.agents/plans/2026-05-30-compose-base-resources-refactor.md:261`  
   **Problem**: The later target table keeps `render_sdl` in `base/graphql/schema.py`, while the authoritative R3 block says SDL render is a build/run-settings phase. The top block supersedes the table, but an executor could still follow the stale row.  
   **Recommendation**: Treat the R3 block as authoritative and update the target layout: `base.graphql` builds live schemas; `compose` renders/checks SDL files.

### Top recommendations
1. Move SDL file rendering/checking out of `base.graphql` and into `compose`, using `GraphQLSchemas.build(name).as_str()` as the seam.

2. Add `GraphQLSchemas` now; it is no longer optional once schema collection, serving, ASGI routing, and SDL rendering share the same state.

3. Split `subscriptions.py` into event type, subscription factory, stream, access gate, and actor resolution modules before adding more GraphQL behavior.

4. Move delete cascade preview computation out of GraphQL and keep `crud()` as a thin strawberry-django shortcut plus delete resolver adapter.

5. Keep `introspection.py` as the sole Strawberry-internal reader and avoid spreading `__strawberry_*_definition__` access into the new classes.
