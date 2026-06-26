# Refine Adoption — Refactor Plan (greenfield, Hasura dialect)

Date: 2026-06-23
Status: Greenfield plan — **no backward compatibility**; rebuild each surface on
refine and delete the old SDK path in the same change. Backend speaks the **Hasura
GraphQL dialect**, driven by the stock **`@refinedev/hasura`** provider. This is the
**execution** plan; decisions/rationale live in
[`refine-adoption-sdk-decomposition.md`](./refine-adoption-sdk-decomposition.md).

> **2026-06-24 target update:** frontend execution has moved to
> [`refine-greenfield-rebuild-plan.md`](./refine-greenfield-rebuild-plan.md).
> This file remains the Hasura dialect/rationale history. When frontend wording
> conflicts with the greenfield plan, the greenfield plan wins.

## Decision: Hasura dialect (settled 2026-06-23, by A/B spike)

An A/B spike (`@refinedev/hasura` vs `@refinedev/nestjs-query`, both stock) settled
the dialect on facts:
- **Aggregates ≈ free:** `strawberry-django-aggregates` already emits the
  `<Model>Aggregate` type that *is* Hasura's `aggregate{count,sum{f},avg{f},…}` — the
  Hasura adapter exposes it in `<model>_aggregate{aggregate,nodes}` with **~0 reshape**
  (lib `aggregation.py` = 143 LOC, mostly docs) vs the nestjs path's **299-LOC**
  reshape.
- **Less backend LOC** overall (aggregate + ~half the filter); mutations/connection ≈ a wash.
- **Portable standard dialect** (`_bool_exp`/`_aggregate`/`x_by_pk`/`_set`) — Angee can
  later run behind real Hasura / its ecosystem, not only one JS provider.
- **sqid seamless, unpatched:** `@refinedev/hasura@7.0.1` roundtrips sqid-as-`id`
  through every pk-centric op (`_by_pk`/`pk_columns`/`where:{id:{_eq}}`) with one
  provider option `idType:"String"` (+ pk-*arg* wire type `String` not `ID`, which is
  what real Hasura does anyway). Both stock providers bind unpatched (13/13 Hasura).

Companions (read for evidence; not duplicated here):
- Design + decisions: `refine-adoption-sdk-decomposition.md`.
- **Backend adapter (built, verified):** `/Users/alexis/Work/angee/strawberry-django-hasura`
  (`0.1.0`; 71 tests; `twine check` PASSED; one-call `hasura_resource(...)` builder; aggregate
  wired free; grouped root/writable allowlist owned by the adapter; stock `@refinedev/hasura` 13/13).
- The earlier nestjs POC (worktree `poc/strawberry-django-nestjs`) proved the
  refine-adoption *pattern* end-to-end and the dialect-agnostic backend work; it is
  **superseded** by the Hasura path but its rebac/fork/crud findings carry over.
- Authorization research: `.agents/notes/rebac-graphql-permission-classes.md`.
- CRUD upstreaming: `.agents/notes/crud-upstream-candidates.md`.
- Aggregate/filter owners: `.agents/notes/data-management-library-research.md`.
- Architecture review of today's `@angee/sdk`: folded into Phase 6 cleanups.

## Resolved clarifications (2026-06-23)

1. **Grouped buckets & facets — use the adapter-owned NDC-shaped grouped root
   (NOT a Hasura `_aggregate` reshape, NOT an Angee hand-rolled resolver).**
   Hasura's `<model>_aggregate{aggregate,nodes}` is *ungrouped*; the standard
   dialect has **no group-by**, and `@refinedev/hasura` / refine expose **no**
   group/facet hook (verified — zero `group_by` anywhere in `@refinedev/*`).
   So grouped buckets ride the `strawberry-django-hasura` companion root emitted
   by `hasura_resource(groupable=[...])`: snake `<model>_groups(where,
   dimensions, limit, offset)` returning `[<model>_group!]!`, with aggregate
   math delegated to `strawberry-django-aggregates`. **Facets** = the same
   grouped field **batched under aliases** in one request (`facet0`/`facet1`/…,
   the existing multi-facet pattern). Both are consumed via `useGroupBy` /
   `useFacets` over `useCustom` + `meta.gqlQuery` (provider-agnostic — the stock
   provider never touches them); extraction carries over against the shared
   `dimensions` + `aggregates` shape; REBAC-scoped via the resource queryset /
   aggregate queryset owner. **Hasura group-by status (researched 2026-06-23):** there is **no consumable native Hasura
   GraphQL group-by today** — v2 has none (#2965), and v3/DDN's GraphQL Interface doesn't surface
   it yet (the NDC spec *defines* grouping, but the Postgres connector / GraphQL mapping is
   pending — enhancement [#10786](https://github.com/hasura/graphql-engine/issues/10786), Oct
   2025; the DDN docs show only the ungrouped `<field>Aggregate`). However, the **NDC grouping
   spec** (`groups`: `dimensions` with an `extraction` fn like year + per-group `aggregates` +
   pre/post `predicate` (= having) + `order_by` on dims or aggregates + before/after
   `limit/offset`) is **semantically the same shape `strawberry-django-aggregates` already
   emits** (group_by spec + granularity + measures + having + group-order + pagination +
   filter-echo). **So: keep grouping on `useCustom` (no provider supports it either way), but
   shape the `<model>_groups` field on the NDC grouping structure** (a `groups`-style result:
   dimension values + per-group aggregates) — future-proofing it for when DDN's native GraphQL
   group-by ships (#10786) and giving **one shape across backends**. The
   ungrouped `_aggregate` stays the portable standard; grouping isn't a
   portable *standard* today, but adapter-owned NDC-shaping makes it
   forward-compatible without an Angee-private dialect.

2. **Snake-case: all the way** (per architect lean). The public/console Hasura schema is
   committed to snake_case **schema-wide** (`hasura_config()`/`SnakeNameConverter`) — root
   fields, input types, args, `_bool_exp` operators, **and node fields**. Authored operations
   become snake_case codegen documents (greenfield rebuilds them anyway). Rationale: one name
   converter per Strawberry schema (mixed casing fights that model), the stock provider's
   `namingConvention:"hasura-default"` expects consistent snake, and snake node fields match
   real Hasura (full portability incl. SQL-column naming). This drops idiomatic camelCase for
   the Hasura-served schemas — an explicit API-style commitment the greenfield "delete the
   old, rebuild" principle absorbs.
   **Mechanism (validated by the `notes_hx` integration):** Angee builds one schema per addon
   bucket with Strawberry's *default camelCase* converter and **no per-surface `StrawberryConfig`
   seam**, so a schema-wide `hasura_config()` cannot be installed while camelCase surfaces still
   coexist (it would re-case them). So **during migration the `hasura_resource` builder pins
   snake `name=` per field** — roots, inputs, args, node fields, **and the
   `strawberry-django-aggregates`-generated `<Model>Aggregate` field names** (mandatory: the
   aggregates compiler maps a selected field name straight back to `model._meta.get_field`, so a
   camelCased aggregate field *breaks at runtime*; the integration pins them via a
   `_pin_snake_wire_names` helper). The schema-wide converter is the **end state** (installed once
   a schema is fully Hasura, then the per-field pins drop); the clean enabler is a **per-bucket
   name-converter seam on `AngeeSchema`**. **2026-06-24: the end-state trigger has fired** — every
   schema is now Hasura (Lane E), so the per-field pins are permanent debt until the seam lands.
   Build the seam (now Lane L, promoted out of the Parking Lot) and delete `pin_snake_wire_names`
   plus its addon call sites; note Angee's `hasura.py` copy is a weaker duplicate of the adapter's
   own `_pin_snake_wire_names` (no cyclic-graph guard), so reuse/install the adapter owner, do not
   keep two. Filter-semantics
   note: the lib maps `_ilike → Django __icontains` (substring), not SQL `LIKE` wildcards — fine
   for refine, but a real-Hasura `_ilike` portability gap to revisit.

3. **`hasura_resource(...)` owner = the library — built.** The one-call declarative builder
   (assembling list + `_by_pk` + `_aggregate` + `insert/update/delete` + `_bool_exp`/`order_by`
   from a model + node type) lives in **`strawberry-django-hasura`** — generic dialect assembly.
   Current signature: `hasura_resource(node, *, model, name=None, filterable, sortable,
   aggregatable, groupable=None, writable=None, insertable=None, updatable=None,
   insert=True, update=True, delete=True, field_id_decode=None, get_queryset,
   get_aggregate_queryset=None, write_backend, id_decode=None, id_column="pk")
   -> HasuraResource`
   (a `(query, mutation, types)` bundle that drops into a schema bucket). Angee supplies the
   **REBAC/sqid hooks as parameters** (the scoped read/aggregate querysets, the authorized
   `write_backend`, the sqid `id_decode`/`id_column`, relation public-id field decoders, and
   explicit insert/update columns mirroring Hasura column permissions). Column→python type defers
   to strawberry-django's `field_type_map` (the owner, fail-fast on unmapped — not a hand-kept
   table), with FK mutation/filter columns treated as scalar target/public-id columns. Phase 0's
   "generalize the builder" is **complete** (expanded grouping/write gates now 75 tests).

4. **Storage `drive` create rule — fixed before Phase 2 (closes Move #6).** The
   `permission create = admin->member + manager` rule is now in `storage/drive` in the storage
   `.zed` (mirroring `storage/backend`), `rebac sync` has run, and the create-path test is green.
   Keep this here as the sequencing lesson for future migrations: standard model writes must have
   their REBAC create rule in place before their CRUD surface is de-elevated.

5. **Operator sequencing — daemon READY + installed.** The operator Go daemon now emits the
   Hasura dialect (`refactor/graphql-hasura`, commit `7505a10`; `_bool_exp`/`_aggregate`/`_by_pk`/
   `_set`/`order_by` verified; binary installed) — so Phase 3 mounts **all three** providers from
   the start (`dataProvider={{ public, console, operator }}`), and the operator console addon
   migrates in Phase 6 against it.
   **Cross-backend grouping — RESOLVED (2026-06-23):** the rebuilt operator converged on the
   NDC `_groups` shape (commit `b0bc392`): `<res>_groups(where: <res>_bool_exp, dimensions:
   [<res>_group_dimension!]!, limit, offset): [<res>_group!]!` — the old `serviceAggregate`/
   `groupBy` removed. The shared grouped shape is settled:
   `strawberry-django-hasura` now emits the same `dimensions`/`[<res>_group]`
   form for Django resources that pass `groupable=[...]`, so one
   `useGroupBy`/`useFacets` spans both providers. Phase 2 must turn that on per
   migrated Django model and delete the old `data_query(...)` grouped roots.

## What is proven / done

- ✅ **`strawberry-django-hasura` lib + one-call `hasura_resource(...)` builder** — standalone,
  PyPI-ready, verified (75 tests, build + `twine check`, ruff/mypy clean). Compose-only over
  strawberry-django + strawberry-django-aggregates; the aggregate is wired **free** (no reshape);
  the builder bakes in per-field snake pinning (incl. the `<Model>Aggregate` fields) and defers
  column→type to strawberry-django's `field_type_map` (the owner; fail-fast on unmapped). Stock
  `@refinedev/hasura` drives it unpatched (13/13); sqid roundtrips via `idType:"String"`.
  **Grouped companion update (2026-06-23):** optional `groupable=[...]` now emits the
  operator/NDC-shaped `<res>_groups(where, dimensions, limit, offset): [<res>_group!]!`
  companion root in the same adapter library, backed by `strawberry-django-aggregates`
  and Hasura `where_to_q`, so Angee does not carry a private grouped resolver.
  **Write-shaping update (2026-06-23):** explicit `writable=[...]` shapes insert/set inputs in
  the adapter, `insertable=[...]` / `updatable=[...]` split Hasura column permissions when needed,
  `insert/update/delete` operation flags remove disallowed mutation roots, FK/public-id field
  decoders keep relation filters and writes generic, and `get_aggregate_queryset` lets aggregate
  math use the REBAC aggregate scope while aggregate `nodes` still come from the normal read scope.
  **Cyclic graph update (2026-06-23):** the adapter's snake-name pinning now tracks visited
  Strawberry object definitions, so cyclic node graphs like `Party -> Handle -> Party`
  terminate at the library owner instead of requiring addon workarounds.
- ✅ **Backend fork/rebac fixes (dialect-agnostic, live in `refactor-refine`):**
  strawberry-django `hotfix-angee-django` (Move #1 `_pk_lookup`/`DEFAULT_PK_FIELD_NAME`,
  Move #2 `__copy__`) + django-zed-rebac **0.12.0** (`for_write()`); pyproject/uv.lock
  repointed; verified live.
- ✅ **`crud.py` simplified onto stock mutations + `for_write()`** — deleted
  `_AngeeCreateMutation`/`_AngeeMutationCloneMixin`/`_resolve_for_write`/`_create_mutation`/
  `coerce_relation_public_ids` (**−121 ln**); `schema --check` ok; 37 + 38 REBAC tests pass.
- ✅ **`permission_classes`/`write_context` redundancy removed (Move #6)** across 13 admin
  CRUD surfaces (integrate/agents/storage); kept on actions/non-model resolvers; integrate
  `.zed` comment fixed; 97 admin/rebac tests pass. **`storage/drive` flag now closed (verified):**
  added `create = admin->member + manager` (mirrors `storage/backend`) + `rebac sync` + de-elevated
  the drive `crud()` pair; 5 drive tests pass. (In practice drive create is admin-only via
  `admin->member` — the `manager` arm needs a per-object tuple — same as `storage/backend`.)
- ✅ **Real-Angee Hasura integration — verified:** a `notes_hx` Hasura resource over the real
  REBAC-scoped `Note` via the lib — **16 REBAC tests + `schema --check`** green (reads/writes use
  the exact Angee crud/rebac path; sqid roundtrips; aggregate free). Confirms the pattern on
  Hasura against real Angee. (Worktree POC; coexists with the now-superseded nestjs resource.)
- ✅ **Operator daemon → Hasura dialect: ready + installed** (`refactor/graphql-hasura`,
  commit `7505a10`; verified `_bool_exp`/`_aggregate`/`_by_pk`/`pk_columns`/`_set`/`order_by`
  present, nestjs markers gone; binary at `/usr/local/bin/angee`). **Rebuilt** to the full
  `hasura_resource` conventions (commits `b0bc392`/`4622ed3`): resource/`_aggregate`/`_by_pk`/
  `pk_columns`/`_set`/`order_by` + the **NDC `_groups` grouped shape** (`services_groups`/
  `sources_groups`; the old `serviceAggregate`/`groupBy` removed). One dialect, one shape.

The architecture is validated end-to-end. This plan rebuilds the product on it and deletes
the hand-rolled half of `@angee/sdk`.

## Greenfield principles (the safety contract)

1. **No backward compatibility.** Each surface is rebuilt on refine and its old SDK path is
   **deleted in the same change**. Old and new never run together in production — no
   coexistence layer, no cross-stack cache bridge.
2. **Authorization is invariant and rebac-owned.** Reads go through the REBAC-scoped manager;
   writes route through the rebac manager + the scoped write queryset (`for_write()`), request
   actor ambient. **No GraphQL `permission_classes`** for standard models
   (`.agents/notes/rebac-graphql-permission-classes.md`). `system_context` only where a write
   must bypass the per-row gate; id-less relation-dependent `create` uses `check_new`.
3. **Compose; modify only what Angee owns.** The strawberry/strawberry-django forks are
   composed; `strawberry-django-aggregates` and `django-zed-rebac` (Angee-owned) may gain
   conveniences. Generic CRUD mechanics moved upstream into the owned forks (Phase 0).
4. **Harmonize to refine/Hasura before adding code.** Generic CRUD, list,
   filter, sort, pagination, mutation, cache invalidation, form state, and table
   state must come from refine/Hasura/TanStack owners. Angee only keeps the thin
   adapters, metadata, routing/chrome, authored operations, and domain-specific
   extensions those owners do not provide.
5. **Every phase has a green gate and a deletion target.** The SDK shrinks monotonically.
6. **Delete aggressively.** Dead/obsolete code goes the moment its replacement is green.

## Verification gates (per phase, as relevant)

```sh
# backend (NOTE: bare `uv run pytest`/`mypy` fail here — use the module form)
uv run examples/notes-angee/manage.py schema --check
uv run examples/notes-angee/manage.py makemigrations --check
uv run python -m pytest -k "schema or graphql or rebac or notes or compose"
uv run python -m mypy angee addons
# frontend (per package)
pnpm run typecheck && pnpm run test && pnpm run build
pnpm --filter <host> run test          # addon-composition.test.tsx (glyph/route collisions)
pnpm --filter @angee/e2e run test      # per migrated surface
```

---

## Phase 0 — Backend adapter + upstream fixes  ✅ DONE

**Goal:** the Hasura adapter library + the generic CRUD mechanics pushed into the owned forks.

- ✅ **`strawberry-django-hasura`** built + verified (above), with the **one-call
  `hasura_resource(...)` builder** (the "generalize the builder" item — done, commit `bc74d3c`).
  Emits the Hasura dialect: `<res>(where, order_by, limit, offset)` + `<res>_aggregate{aggregate,
  nodes}`, `<res>_by_pk`, `insert_<res>_one`/`update_<res>_by_pk(pk_columns,_set)`/
  `delete_<res>_by_pk`, `<res>_bool_exp` (`_eq/_neq/_gt/_in/_like/_ilike/_is_null/_and/_or/_not`),
  `order_by` enum, snake-case names off the resource, `idType:"String"` + an `id_decode` hook.
- ✅ **Upstream fork fixes** (the `crud-upstream-candidates.md` analysis — almost all of
  `crud.py` was compensation for one strawberry-django pk-lookup bug): Move #1 (`_pk_lookup`
  honors `DEFAULT_PK_FIELD_NAME`), Move #2 (`__copy__` preserves `key_attr`/`argument_name`),
  Move #3 (`RebacQuerySet.for_write()`). Move #4 (`crud.py` onto stock mutations) done, −121 ln.
- ✅ **Move #6** (delete the admin `permission_classes`+`write_context` redundancy) done; one
  flagged surface (`storage/drive`, above).
- **Decisions recorded:** Move #5 — keep `DeletePreview`/`delete_by_public_id` in Angee (no
  rebac relocation). Per-field `Count(field)` — deferred (row-count mapping is faithful for
  non-null fields; add when a nullable count is user-facing).
- ✅ **Wired into main** (`refactor-refine`): `strawberry-django-hasura` added to `pyproject.toml`
  (editable path source — becomes a git/PyPI source on publish) + `uv.lock` + a `docs/stack.md`
  Backend row; `import` resolves, `schema --check` ok (no consumer yet — pre-wired for Phase 2).
- **Gate:** lib `pytest` (65) + stock `@refinedev/hasura` client (13/13) green; the real-Angee
  `notes_hx` resource passes `schema --check` + 16 REBAC tests (verified).

## Phase 1 — Backend metadata artifact (owned by `angee.graphql`) ✅ DONE

**Goal:** the frontend stops introspecting/inferring; the backend emits the truth.

- [x] Start the authoritative per-model **`angee.resources` metadata artifact** via
      `angee.graphql` schema extensions — **not** the build-time composer (runtime must not
      import it). The verified first slice carries schema/resource roots, relation axes,
      provider/schema name, and notes emission.
- [x] Finish the field/root capability slice: field kind/widget, explicit
      read/write/filter/sort/create/update/required-on-create capability, root-owned
      create/update/required/revision field lists, `groupOrder`, notes snapshot coverage, and
      frontend deletion of SDL root/input-shape inference.
- [x] Finish the group/measure metadata slice: backend-owned group axes and NDC-style
      dimensions, aggregate/default measures, frontend group/facet consumption, relation/date
      dimension snapshots, numeric measure snapshot, and focused backend/schema/frontend gates.
- [x] Finish the backend default-sort slice: `Meta.ordering` terms exposed by the order input
      emit as `defaultSort`, SDK validation rejects non-sortable default-sort fields, and backend
      metadata rejects unknown/to-many group axes plus unknown aggregate-measure paths.
- [x] Consume backend-owned `defaultSort` at the page/view-state owner: model-backed
      list surfaces use it as the default resource order when URL sort and explicit
      `order` are absent, with focused `@angee/base` tests/typecheck green.
- [x] Settle default view and page-owned URL defaults at the page/view-state owner:
      `defaultView` is declarative on page/list surfaces, route serialization compares
      against page defaults, and default page size no longer leaks into the URL when
      another default writes search state.
- [x] Tighten resource field kind emission for to-many node fields: the backend
      artifact now marks GraphQL/Django to-many fields as `list` instead of
      misdescribing them as to-one `relation` fields; relation lists carry no scalar
      or many2one widget claim.
- [x] Add duplicate-name metadata validation at the backend emission owner:
      data-query filter/order/aggregate/group declarations; resource create/update/
      required/revision declarations; and explicit/generated resource field names.
- [x] Reject unsupported to-many field paths during metadata emission: group axes
      and aggregate measures now fail fast when any path segment traverses a
      to-many relation, not only when the final field itself is to-many.
- [x] Tighten resource scalar ownership: generated resource fields now read scalar
      families from the Strawberry surface (so public `id` is `ID`, not a model
      integer), while model-path group dimensions fail fast on unsupported Django
      field classes instead of guessing `String`.
- [x] Finish the next surface-field validation slice: computed Strawberry enum
      fields classify as `enum`, computed object/forward-reference fields classify
      as `relation`, and unsupported custom scalar resource fields fail fast instead
      of emitting scalar-less scalar metadata.
- [x] Complete the remaining fail-fast validation work for unsupported field/axis
      classes: explicit resource field metadata must stay inside the generated
      artifact vocabulary, and non-PK aggregate fields must map to supported
      frontend measure families instead of being marked aggregatable by string.
- [x] Keep filter/order/aggregate/group **mechanics** in their owners —
      emit/shape only: list/filter/order roots remain strawberry-django /
      `strawberry-django-hasura` owned, aggregate math stays in
      `strawberry-django-aggregates`, grouped Hasura roots are adapter-owned via
      `hasura_resource(groupable=[...])`, and metadata only names, validates,
      and serializes the resulting facts.
- **Gate so far:** artifact emitted for notes; notes metadata snapshots + focused
  backend/schema/frontend gates are green for the field/root, group/measure,
  backend default-sort, page/view default-sort consumption, and default-view/page
  URL-default slices, to-many list field metadata, and duplicate-name metadata
  validation plus unsupported to-many field-path/scalar-class/custom-scalar
  rejection plus explicit field/aggregate-measure vocabulary validation. Phase 1
  closure gates: 96 focused backend tests, `@angee/sdk` tests/typecheck,
  `@angee/base` tests/typecheck, notes metadata test, and `schema --check`
  green (2026-06-23).
- **Deletes so far:** `model-metadata.tsx`'s heuristic root-field and input-shape inference are
  deleted after consumers moved to `angee.resources`. The broader SDK metadata owner is still
  thinned/deleted in Phase 6.

## Phase 2 — Backend: Hasura resources for every model (replace `data_query`/`crud`)

**Goal:** the Angee schema speaks the Hasura dialect for every model, with authorization unchanged.

- [x] Add the Angee-owned bridge from `HasuraResource` to the Phase 1
      `angee.resources` artifact before the first model migration. The external
      library owns Hasura list/detail/aggregate/mutation mechanics; `angee.graphql`
      owns resource metadata, custom roots, and schema-bucket composition. The
      bridge lives in `angee.graphql.data.hasura`, pins node fields to snake
      during mixed-schema migration, attaches query/mutation metadata to the
      generated `HasuraResource`, and is covered by a focused metadata test
      plus `schema --check` / notes metadata gates (2026-06-23).
- [x] Mainline `Note` migrated (2026-06-23): old `data_query(...)` /
      `crud(...)` roots removed; `notes`, `notes_by_pk`, `notes_aggregate`,
      `notes_groups`, `insert_notes_one`, `update_notes_by_pk`, and
      `delete_notes_by_pk` now come from `hasura_resource(...)`; grouped buckets
      use adapter-owned `groupable=["status", "updated_at"]`; writes use the
      shared `AngeeHasuraWriteBackend`; `delete_note`, `noteRevisions`, and
      `noteChanged` remain authored/custom roots.
- [x] IAM `User` + `Group` migrated (2026-06-23): old IAM `data_query(...)`
      roots and old `createUser`/`updateUser` CRUD mutations removed; `users`,
      `users_by_pk`, `users_aggregate`, `users_groups`, `insert_users_one`,
      `update_users_by_pk`, `delete_users_by_pk`, plus the matching `groups*`
      roots now come from `hasura_resource(...)`. User writes keep the IAM-owned
      password hashing backend; `delete_user` remains an authored delete-preview
      operation; `userChanged` remains the subscription. The IAM web authored
      users document and Users page field declarations were moved to the Hasura
      snake surface and codegen was regenerated.
- [x] Storage `Drive` + `Folder` + `File` + `Backend` migrated (2026-06-23):
      old storage `data_query(...)` roots and old `crud(...)` mutations removed;
      `drives`, `folders`, `files`, and console-only `backends` now expose
      Hasura list/by-pk/aggregate/groups plus enabled insert/update/delete roots
      from `hasura_resource(...)`. File insert is disabled because creation
      belongs to the upload protocol; `file_upload_begin`,
      `file_upload_finalize`, `restore_file`, `purge_file`, `fileChanged`, and
      MIME taxonomy stay authored Storage-owned operations. Folder insert uses
      the `Folder.objects.create_in_drive` owner through a write backend; relation
      public-id filters/writes use the generic adapter/Angee decoder seam. Storage
      web documents moved to direct Hasura arrays and snake fields; codegen was
      regenerated.
- [x] Parties `Party` + `Person` + `Organization` + `Handle` + `Address` +
      `Affiliation` + `Folder` + `Directory` migrated (2026-06-23): old parties
      `data_query(...)`, handwritten paginated roots, and generic `crud(...)`
      mutations removed; `parties`, `people`, `organizations`, `handles`,
      `addresses`, `affiliations`, `contact_folders`, and `directories` now
      expose Hasura list/by-pk/aggregate/groups where applicable plus enabled
      insert/update/delete roots from `hasura_resource(...)`. Person/Organization
      and related contact writes use `AngeeHasuraWriteBackend` and public-id
      relation decoding; the CardDAV connect mutation remains authored because it
      creates credential/vendor/directory rows transactionally and probes the
      backend. Parties web declarations moved to snake fields and Hasura relation
      filters; codegen was regenerated.
- [x] Messaging `Channel` + `Message` + `Thread` migrated (2026-06-23): old
      messaging `data_query(...)`, handwritten channel paginated roots, and
      generic `crud(...)` mutations removed; `channels`, `messages`, and
      `threads` now expose Hasura list/by-pk/aggregate/groups where applicable
      plus message/thread update/delete roots from `hasura_resource(...)`.
      Message creation remains manager/backend-owned ingest, so generic insert
      roots stay disabled. Parts, participants, edges, reactions, and metrics
      remain nested read projections reached through their owning message/thread.
      Messaging web declarations moved to snake fields and Hasura relation
      filters; codegen was regenerated.
- [x] Replace each addon's `data_query(...)`/`crud(...)` with
      `hasura_resource(...)` plus separately owned custom roots that are **not**
      part of the portable Hasura resource primitive (`revisions`, `changes`,
      delete preview/custom actions). Grouped roots are still custom GraphQL
      roots from refine's perspective, but their Django emission belongs to the
      `strawberry-django-hasura` adapter via `groupable=[...]`, not Angee glue.
      Delete the old resource in the same change. Resource/type names are the canonical Hasura plural form
      (`notes`, `notes_bool_exp`, `notes_aggregate`, `insert_notes_one`, …).
- [x] For each remaining model, reads go through the RebacManager auto-scope, aggregate math
      uses the aggregate queryset owner, writes go through stock strawberry-django mutations plus
      `for_write()` and the rebac signal (**no `permission_classes`**), and sqid pk args stay
      `String` for `idType:"String"` compatibility.
- [x] For any model whose old `data_query(...)` exposed groups, pass
      `groupable=[...]` to `hasura_resource(...)` in the same slice so
      `<model>_groups` uses the settled operator/NDC contract (`dimensions` +
      per-group aggregates). This is owned by `strawberry-django-hasura`, with
      aggregate math delegated to `strawberry-django-aggregates`; Angee should
      not carry a local grouped resolver.
- [x] Repo-wide addon schema audit (2026-06-23): no addon schema still calls
      `data_query(...)` or generic `crud(...)`; remaining occurrences are the
      core compatibility primitives and tests, to be deleted or thinned in the
      later frontend/SDK deletion lanes when their callers are gone.
- **Gate per model:** `schema --check`, REBAC **read + write** tests (authorized passes,
  anonymous/non-owner denied/empty), metadata artifact snapshot/update, `uv run python -m pytest`.
- **Deletes:** `data_query`/`crud` calls per migrated model.

## Phase 3 — Frontend foundation: refine + `@angee/data`

**Goal:** the host runs on refine; the thin frontend glue exists.

- [x] Deps + `docs/stack.md` rows: `@refinedev/core`, **`@refinedev/hasura`**,
      `@refinedev/react-table`, `@refinedev/react-hook-form`, `graphql-request`,
      `react-hook-form`, `@hookform/resolvers`, `zod`, and `graphql-ws@5`.
      `urql` is recorded as transitional authored-operation transport only
      (react-query/refine owns cache/invalidation); `graphql-request` is the
      Hasura provider transport; zod is the form resolver owner.
- [ ] **`@angee/data`**: the stock `@refinedev/hasura` provider config (`namingConvention`,
      **`idType:"String"`**, `meta.gqlQuery` for authored ops); `liveProvider` via
      `createLiveProvider(graphql-ws)` driven by `<model>Changed`; the custom hooks (all over
      `useCustom` + authored `meta.gqlQuery`) — `useAggregate` → the native
      **`<model>_aggregate`** (ungrouped, no reshape); `useGroupBy`/`useFacets` → the custom
      **`<model>_groups`** root (grouped buckets / aliased facets, per clarification #1);
      `useDeletePreview` → the Angee `DeletePreview` op; the **metadata loader** (Phase 1
      artifact → refine `resources` + field/widget resolution).
- [x] First `@angee/data` slice (2026-06-23): package boundary created; stock
      Hasura provider factory pins `namingConvention:"hasura-default"` +
      `idType:"String"`; HTTP uses `graphql-request` with Angee session/CSRF
      auth at the transport boundary; provider maps emit refine's required
      `{ default, ...named }` shape; metadata maps `angee.resources` to refine
      resources using the Hasura list root and `meta.dataProviderName`; explicit
      ws/wss endpoints are preserved while HTTP endpoints derive ws/wss.
      Generated metadata JSON is normalized once at the `createApp` boundary via
      `defineAngeeSchemaMetadata`, so app hosts can pass emitted metadata
      directly while SDK/refine consumers still receive the strong resource
      contract.
- [x] Custom Hasura operation slice (2026-06-23): `@angee/data` now owns
      refine-native `meta.gqlQuery` request builders and hooks for the custom
      resource operations refine/Hasura does not model as generic CRUD:
      `aggregateRequest`/`useAngeeAggregate` over `<resource>_aggregate` and
      `groupByRequest`/`useAngeeGroupBy` over NDC-shaped
      `<resource>_groups(dimensions, where, limit, offset)` now receive generated
      documents; `useAngeeFacets` runs TanStack `useQueries` over generated group
      documents instead of a runtime aliased grouped request. The runtime
      `operations.ts` module no longer parses or assembles GraphQL strings for
      action, aggregate, delete-preview, revision, list, group, or facet
      execution.
- [x] Delete-preview metadata/hook slice (2026-06-23): `angee.graphql.deletion`
      now owns an `attach_delete_preview_metadata(...)` helper for authored
      cascade-preview mutations. `angee.resources.roots.delete` stays the
      generic Hasura `delete_<resource>_by_pk` root, while
      `roots.deletePreview` names authored preview roots (`delete_note`,
      `delete_user`, `delete_vault`, `delete_page`,
      `delete_external_account`, `delete_credential`). `@angee/data` consumes
      that root through `deletePreviewRequest` / `useAngeeDeletePreview` over
      refine `useCustomMutation` + `meta.gqlMutation`; no frontend name guessing.
      Integrate preview roots were pinned to snake names for the Hasura schema
      contract.
- [x] Refine liveProvider slice (2026-06-23): `@angee/data` now exposes an
      Angee change-event `LiveProvider` adapter over `graphql-ws`, driven by
      `angee.resources.roots.changes`. `createApp` mounts it when the
      subscription schema config opts into `live`, sets refine `liveMode:"auto"`
      only when a provider exists, and the notes host enables it for the console
      schema. This is intentionally a thin bridge from Angee's existing
      `<model>Changed` roots into refine's invalidation lifecycle; resources
      without a `changes` root no-op instead of blind-subscribing.
- [x] Refine list hook slice (2026-06-23): the shared model-backed list owner
      now calls `@angee/data`'s refine-backed `useResourceList`, which delegates
      generic list fetching, pagination, sorting, filtering, totals, query keys,
      and live invalidation to refine `useList` + stock `@refinedev/hasura`.
      Angee keeps only the resource-metadata lookup, field-selection mapping,
      and UI-filter/order vocabulary translation into refine's public
      `CrudFilters`/`CrudSorting` shape. The shared `@angee/base` surfaces
      (`data-view-surface`, grouped leaf rows, related rows, relation options,
      and record-navigation sync) moved to this owner, and the old SDK
      `useResourceList` implementation/export/tests were deleted in the same
      slice.
- [x] **TanStack Router `routerProvider`** (~4 methods: `go`/`back`/`parse`/`Link`)
      added in `@angee/data` and mounted by `createApp`; `<Refine>` now lives
      inside the root TanStack route so refine router hooks run under router
      context instead of outside `RouterProvider`.
- [x] Multi-schema named providers `dataProvider={{ public, console, operator }}` (operator
      daemon emits Hasura too → same provider).
- [x] Mount `<Refine>` as the host's data/runtime layer. The old urql provider
      remains only for unmigrated SDK/authored paths until Phase 4/6 delete it;
      generic new data work should use refine.
- **Gate so far (2026-06-23):** `@angee/sdk` typecheck + model-metadata tests,
  `@angee/data` typecheck/tests, `@angee/base` typecheck/tests,
  `@angee-example/notes-host` typecheck/build, and `pnpm peers check` are green.
  Refine list-hook slice gates: `@angee/data` typecheck/tests, `@angee/sdk`
  typecheck + focused resource hook/invalidation tests, `@angee/base` typecheck
  + focused DataPage/RelatedRowsList/DeleteBulkFlow tests, `@angee-example/notes-host`
  typecheck, and `pnpm peers check`. Remaining Phase 3 proof: one
  model-backed list rendering via refine against the real backend/browser.

### Phase 3b — Auth / preferences / i18n on refine (explicit)

**Goal:** the cross-cutting providers exist before their SDK modules are deleted.

- [ ] refine `authProvider` + identity / login / logout (**with cache clearing**), preferences
      mutation, and `i18nProvider` with namespace-**fallback** (`useNamespaceT` equivalent).
- **Gate:** login / logout / preferences / i18n tests.
- **Deletes (later):** `auth.ts`, `auth-hooks.ts`, `preferences.tsx`, `i18n.ts`.

## Phase 4 — `@angee/base` rebinds on refine headless

**Goal:** the shared rendered views bind to refine; design system unchanged (Base UI + Tailwind; **never** `@refinedev/mui`).

- [ ] `ListView`/`RowsListView`/`DataPage` on **`@refinedev/react-table`** (TanStack Table —
      already a base dep), keeping grouping/board/selection.
- [ ] `FormView` on **`@refinedev/react-hook-form` + zodResolver** — preserve the declarative
      `<Field>`/`<Group>`/`showWhen` DSL; only the state engine changes.
- [ ] Keep the **board/grouped/favorites** view-state in `@angee/base`; delete the
      sort/filter/paginate overlap (now `useTable`).
- [ ] Rebind `RecordView`/detail + state surfaces.
- **Gate:** storybook + base vitest; a model-backed `DataPage` end-to-end.
- **Deletes:** the sort/filter/paginate half of `data/view-state.ts`.

## Phase 5 — `@angee/runtime` (addon composition; runtime owns routing)

**Goal:** addon composition maps onto refine `resources` + Angee registries, runtime still owning routing/chrome.

- [ ] **`@angee/runtime`**: `defineAddon`/`composeAddons` → widget/slot/preview/form/icon
      registries, and **derive** refine `resources` from the runtime. Runtime KEEPS ownership of
      route-target validation, chrome/breadcrumbs, shell, model-route indexing.
- **Gate:** `addon-composition.test.tsx` proves equivalent validation/breadcrumbs/routes/shell.
- **Deletes:** only the menu/route registry code refine `resources` genuinely subsumes.

## Phase 6 — Migrate addons, then delete the old `@angee/sdk` (the DRY payoff)

**Goal:** every product surface on refine + codegen documents; the hand-rolled SDK half gone.

Per addon (notes → iam → storage → integrate → agents → parties → knowledge → messaging → operator console):
- [ ] Frontend: switch the addon's pages/resources to refine hooks + codegen-authored
      `graphql()` documents (`meta.gqlQuery`); **delete** the addon's bespoke `useAuthored*`/
      action/`ctx.record→mutate→refresh` paths in the same change.
- [ ] **Codegen + rename sub-step:** `documents*.ts` routing; ownership of `@angee/gql/<schema>`
      helpers + `DocumentVariables` after SDK deletion; per-model `<Model>Type`→`<Model>` rename;
      per-schema (public/console/operator) codegen gates.
- [ ] Operator console: same stock `@refinedev/hasura` provider as the `operator` named provider
      (daemon now emits Hasura).
- [ ] After all addons migrate: **delete the superseded `@angee/sdk` modules** (ledger below).
- **Gate per addon:** `schema --check`, `pnpm typecheck/test/build`, addon e2e, REBAC tests.
  **Final:** repo-wide green + **net line drop reported**.
- **Deletes:** per-addon bespoke code; finally ~60–70% of `@angee/sdk`.

---

## Deletion ledger (`@angee/sdk` → owner)

| Today | Replaced by | Phase |
|---|---|---|
| `resource-hooks.ts` list path | `@angee/data` `useResourceList` over refine `useList` | 3 ✅ |
| `resource-hooks.ts` record/revisions/mutations | refine `useOne/use{Create,Update,Delete}` | 6 |
| `authored-hooks.ts` | refine `useCustom`/`useCustomMutation` | 6 |
| `action-hooks.ts`, `action-result.ts` | `useCustomMutation` + thin action helper in `@angee/data` | 6 |
| `document-query/mutation/subscription.ts`, `disabled-documents.ts` | refine + transport client | 6 |
| `stable-deps.ts` | react-query (refine) | 6 |
| `graphql-client.ts`, `graphql-provider.tsx`, `cache-config.ts`, `schema-object-types.ts` | refine dataProvider + transport | 6 |
| `relay-invalidation.tsx`, `relay-registry.ts` | refine `liveProvider` + `invalidate` | 6 |
| `selection.ts` | codegen `TypedDocumentNode` via `meta.gqlQuery` | 6 |
| `model-metadata.tsx` (root/input-shape inference) | backend metadata artifact | 1 ✅ |
| `model-metadata.tsx` (remaining metadata loader/validation) | `@angee/data` loader, then delete/thin residual SDK owner | 3/6 |
| `model-metadata.tsx` (field→widget classification) | **kept, thin** in `@angee/data` | — |
| `data/view-state.ts` (sort/filter/paginate) | `@refinedev/react-table` | 4 |
| `data/view-state.ts` (board/grouped/favorites) | **moved** to `@angee/base` | 4 |
| `data/graphql-source.ts` | refine dataProvider | 6 |
| `data/facets.ts` | `useFacets` in `@angee/data` (logic reused) | 6 |
| `data/local-source.ts`, `data/query.ts` | refine in-memory provider / thin kept | 6 |
| `aggregates.ts`, `aggregate-extract.ts` | `useAggregate/useGroupBy` in `@angee/data` (native `<model>_aggregate`, no reshape) | 6 |
| `auth.ts`, `auth-hooks.ts`, `preferences.tsx` | refine `authProvider` + identity/login hooks | 3b/6 |
| `i18n.ts` | refine `i18nProvider` (i18next) | 3b/6 |
| `runtime.ts`, `define-addon.ts` | `@angee/runtime` (thinner; routing kept) | 5 |
| `resource-result.ts`, `resource-types.ts`, `validation-errors.ts` | `@angee/data` result types + refine | 6 |
| `make-context.ts`, `error-message.ts`, `use-busy-run.ts` | small utils kept or replaced | 6 |
| Angee `crud.py` (mutation subclasses, clone mixin, `coerce_relation_public_ids`) | strawberry-django fork fixes + `for_write()` → **−121 ln done ✅**; `DeletePreview`/`deletion.py` **kept in Angee** (Move #5 declined) | 0 |

**End state of the bespoke surface:** `@angee/data` (provider config + custom hooks + metadata
loader), `@angee/runtime` (composeAddons + registries + routing), `@angee/base` (design system +
refine bindings); backend `strawberry-django-hasura` + the `angee.graphql` metadata artifact + a
much-thinner `crud`. Everything generic is rented from refine + TanStack + Codegen + graphql-ws + i18next.

## `docs/stack.md` changes (with the manifest, same change)

- **Done:** backend `strawberry-django-hasura` is in `pyproject.toml`/`uv.lock`
  and `docs/stack.md`.
- **Done:** `@refinedev/core`, **`@refinedev/hasura`**, `@refinedev/react-table`,
  `@refinedev/react-hook-form`, `graphql-request`, `react-hook-form`,
  `@hookform/resolvers`, `zod`, and `graphql-ws@5` are in the JS manifests and
  lockfile; `docs/stack.md` is reconciled.
- **Done:** `urql` → transitional authored-operation transport only
  (react-query/refine owns cache/invalidation).
- **Done:** TanStack Form → transitional current form engine; `react-hook-form`
  + zod is the Phase 4 form resolver owner.
- **Naming:** **snake_case all the way** for the Hasura-served public/console schemas
  (`hasura_config()` schema-wide — roots, inputs, args, `_bool_exp` ops, node fields; authored
  ops become snake codegen docs — see clarification #2). Public id stays sqid with pk-args typed
  `String` (`idType:"String"`); the `<Model>Type`→`<Model>` rename folds into the snake pivot.

## Risks & mitigations

- **Cache model change** (normalized graphcache → react-query): greenfield removes the
  *coexistence* version; remaining work is each migrated list refreshing via refine
  `invalidate` + `liveProvider` (the `changes()` subscriptions already exist). Gate each list.
- **Auth/i18n parity** (Phase 3b): reproduce the SDK auth/preferences/i18n contracts before deletion.
- **`react-hook-form` re-bind of `FormView`:** DSL stays, engine changes — port first (Phase 4) with full form-test coverage.
- **Router binding:** no official refine TanStack Router provider — the
  ~4-method `routerProvider` is written and mounted inside the root TanStack
  route; keep it there unless refine ships an official owner.
- **`<Model>` rename + codegen routing:** sequenced per model in Phase 6 with per-schema gates.
- **`idType:"String"` consistency:** type *all* sqid pk-args `String` uniformly (output `id` stays `ID`).
- **Fork divergence:** the strawberry-django fork carries Angee fixes — keep them minimal/documented.
- **Django grouped surface:** the operator `_groups` shape is settled and the
  Django adapter now emits the same NDC-style `dimensions`/`[<res>_group]` form
  via `groupable=[...]`; the remaining risk is per-resource migration plus
  frontend extraction updates, not a bespoke Angee backend implementation.

## Definition of done (per AGENTS.md)

- Architecture gate satisfied per phase; each change at its owning level.
- New behavior composes refine/TanStack/locked deps; exceptions named + documented.
- Net shape reported: modules deleted, callers thinned, line delta.
- Generated outputs regenerated from source; `schema --check` clean.
- Names normalized (`<Model>` rename complete); `docs/stack.md` reconciled with manifests.
- Backend/frontend/schema/e2e gates green, or the handoff states why not.

## Open decisions

1. **Addon migration order** (Phase 6). Lean: notes → iam → storage → knowledge →
   integrate → agents → parties → messaging → operator console. Integrate,
   Agents, Parties, and Messaging are now closed on the Hasura resource contract
   for their current model-backed surfaces.

*(Resolved: dialect = Hasura; operator daemon reshaped to Hasura — one dialect, one provider;
frontend transport = stock `@refinedev/hasura`/`graphql-request`; form resolver owner = zod.)*

## Next action

**2026-06-24 review correction (keystone).** Three independent reviews found the
remaining work is adding new owners without deleting old ones — a dual
refine+urql data/cache/invalidation engine running at once, a sideways-lifted
GraphQL document builder in `@angee/data`, and adapter/aggregates names
re-derived in `hasura.py`/`metadata.py` instead of read off the built types.
Before continuing addon migration, **collapse the dual live/data engine onto
refine + react-query and delete urql + the relay-registry** (the keystone that
unblocks the rest of the SDK deletion and stops `@angee/data` depending on
`@angee/sdk`). Then project `ModelMetadata` from the backend artifact, land the
name-converter seam, and fold the backend owner corrections. Full plan: Lane L +
"Review Corrections (2026-06-24)" in
[`refine-adoption-hasura-todo.md`](./refine-adoption-hasura-todo.md); evidence:
[`refine-adoption-library-leaning-findings.md`](../notes/refine-adoption-library-leaning-findings.md).
