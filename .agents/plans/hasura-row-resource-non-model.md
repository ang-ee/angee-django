# Plan: Hasura resources from non-model (computed) sources

## Goal & payoff

Make every authored (non-model) list query present the **same Hasura list
contract** a model-backed resource does (`where: _bool_exp`, `order_by`,
`limit`/`offset`, `_aggregate { aggregate { count } }`, `<res>_by_pk`), so the
frontend drives them through the rented `@refinedev/hasura` `useList` path like
every other resource. The payoff is a **deletion**: the hand-rolled client-side
filter/sort/paginate engine `packages/base/src/views/local-rows.ts` and the
bespoke `AuthoredRowsList` path collapse into the one `useList` path. The same
uniform surface is then queryable by **MCP** tools under the REBAC actor
(`angee.mcp.graphql`) with no per-source tool.

## Converged architecture (two repos — find-the-owner)

`strawberry-django-hasura` is an **Angee-owned sibling repo**
(`github.com/ang-ee/strawberry-django-hasura`) and **owns the Hasura dialect
mechanics**; its `resource.py` calls the surfaces "model-independent primitives"
and `CONTRACT.md` mandates it "adds no rebac/Angee imports." So the dialect logic
lives there; Angee supplies only the data and the pydantic/metadata glue.

**In `strawberry-django-hasura` (generic, Angee-agnostic, no pydantic):**
- A `run_query`-backed resource builder — sibling to `hasura_resource(model=…)`.
  Inputs: a `node` type + a field/filter/sort **spec** + a `run_query` callable
  (instead of `model=` + queryset). Generates the *same* SDL
  (`_bool_exp`/`order_by`/node-list/count `_aggregate`) reusing its existing
  `comparisons`/`ordering`/`connection` surfaces.
- **Hosts the one genuinely-new piece**: an in-memory `_bool_exp` evaluator
  `apply(rows, where, order_by, limit, offset)` (the Python sibling of
  `filtering.where_to_q`) **plus** `to_hasura_dict(where)` (serialize a parsed
  `_bool_exp` back to a Hasura `where` dict for pushdown). The bounded dialect
  evaluator lives with the dialect, owned once, beside `where_to_q`.
- Count-only aggregate for the run_query path (does NOT use the SQL
  `AggregateBuilder` — see "Decisive finding").

**In Angee (`angee/graphql/data/hasura.py`) — thin wrappers:**
- `hasura_model_resource` — **rename** of today's `hasura_resource`; wraps the
  library's model builder. Sweep all call sites. Upstream's vendored
  `hasura_resource` is untouched.
- `hasura_pydantic_resource` — wraps the library's `run_query` builder: derives
  the spec + node from a **pydantic** model (SSOT for fields/types/validation;
  node via `strawberry.experimental.pydantic.type`, verified working), attaches
  `DataResourceMetadata` (model-optional path), and supplies each source's
  `run_query`. Pydantic + Angee metadata stay OUT of the lean library.

**`run_query` is the provider-pushdown seam** ("all the way down"): a computed
list source does `return apply(rows, where, order_by, limit, offset)`; the
operator daemon does `return daemon.query(to_hasura_dict(where), …)` — it
*receives* the `_bool_exp`. **queryish is optional** — just one way to implement
a `run_query` (e.g. a daemon REST pushdown); NOT a core dependency, so it is not
added to the stack.

## Decisive finding (why count-only, why a separate builder)

`strawberry-django-hasura`'s `<res>_aggregate`/`<res>_groups` are welded to the
SQL aggregate compiler (`strawberry-django-aggregates` chains real
`.values().annotate().aggregate()` / `.distinct().count()`) — verified against
installed source. **No in-memory/fake queryset can satisfy it.** But these
console lists need only `aggregate.count` for pagination (never sum/avg/group),
so the run_query builder uses a **trivial count aggregate** and never touches the
welded compiler. This is why a separate builder is unavoidable (you cannot get
count-only out of the model builder) and why "reuse the model builder over a
dynamic model" does NOT fully work.

Rejected alternatives (researched): **ephemeral SQLite real table** (reuses
everything incl. real aggregates, but the repo is single-DB by construction, no
DB router exists, REBAC/Hasura are table-bound — heavy new infra for a
capability we don't need); **`managed=False` over a DB view** (computed
app-registry/daemon/REBAC rows are not SQL-view-expressible); **hand-rolled
evaluator in a consumer** (re-derives what the dialect owner should own —
resolved by hosting `apply` in the library).

## Source classification (owner-split, verified)

| Source | Backing | Path |
|---|---|---|
| Operator daemon (`operatorConnection` + daemon rows) | Foreign daemon, already serves Hasura SDL over its own transport | **No change** — already Hasura |
| Raw REBAC relationships (`relationships`) | `active_relationship_model().objects` — real queryset | **native `hasura_model_resource`** (read-only) |
| Resources ledger (`resourceLedger`) | `Resource.objects.ledger_page()` — concrete model | **native `hasura_model_resource`** (read-only) |
| Platform explorer (addons/models/fields/edges) | Computed from `apps.get_app_configs()` — no table | **`hasura_pydantic_resource`** |
| REBAC roles / grants / `rebac_schema` / `iam_overview` | Tuples joined with schema-AST labels in no table | **`hasura_pydantic_resource`** |

## Owner map

- Hasura dialect mechanics → `strawberry-django-hasura` (the new run_query
  builder + `apply` + `to_hasura_dict` live here).
- Row shape SSOT → a pydantic `BaseModel` per computed source (Angee side).
- GraphQL node → `strawberry.experimental.pydantic.type(RowModel)`.
- Resource metadata → `angee/graphql/data/metadata.py` — gains a model-optional
  path (`model=None` + required `model_label`); `model` is already `{"wire":
  False}` so nothing model-shaped reaches the frontend.
- Frontend resource decision → already keys on `DataResourceMetadata.roots.list`
  (non-null ⇒ `useList`) + `modelLabel`; a unique label (e.g. `platform.addon`)
  flows through `useList` identically.
- Schema registration → unchanged AppConfig `schemas` bucket seam.
- Admin gating → moves to `run_query`/provider scope (holds for `useList` AND
  MCP), replacing field-level `permission_classes` for the converted queries.

## Stages

### Stage 0 — model-optional metadata (Angee `metadata.py`)
`make_data_resource_metadata(model: type[Model] | None = None, …)`; require
`model_label` when `model is None`; guard `model._meta` reads
(`_model_label_parts`, `_resource_fields`, `_default_sort`, `_merge_data_resource`);
`DataResourceMetadata.model` → `… | None`. Tests: builds + serializes with
`model=None`, `model` stays off the wire.

### Stage 1 — sibling library: generic run_query resource (strawberry-django-hasura)
Branch the sibling; add under `[tool.uv.sources]` a temporary
`strawberry-django-hasura = { git/branch | path }` pin. Implement:
- `apply(rows, where, order_by, limit, offset)` + `where_matches(where, row)` —
  in-memory evaluator mirroring `filtering.where_to_q`/`_LOOKUPS` semantics
  (`eq`/`neq`/`gt..lte`/`in`/`nin`/`like`/`ilike`/`is_null` + `_and`/`_or`/`_not`);
  fail-fast on an unmapped operator (never silently widen).
- `to_hasura_dict(where)` — parsed `_bool_exp` → Hasura `where` dict (pushdown).
- The run_query resource builder: `_bool_exp`/`order_by`/node-list + count
  `_aggregate` from a spec; resolver calls `run_query`. Reuse `_input_type`,
  `_pin_snake_wire_names`, comparison classes, `OrderBy`, pagination.
- Tests in the sibling repo (SDL parity vs model resource; evaluator parity vs
  `where_to_q` on a fixture). Update sibling `CONTRACT.md`/`CHANGELOG.md`; cut
  `0.3.0`.

### Stage 2 — Angee wrappers
Rename `hasura_resource` → `hasura_model_resource` + call-site sweep. Add
`hasura_pydantic_resource(RowModel, *, run_query, filterable, sortable, …)`:
pydantic → spec + node, count aggregate, attach metadata (Stage-0 path),
register in a schema bucket. Unit tests.

### Stage 3 — convert one computed source end-to-end (platform addons)
`PlatformAddonRow` pydantic model; `run_query` wraps existing
`build_platform_addons` (returns all rows; `apply` filters/sorts/pages). Register
in the platform `console` bucket. **Verify `useList` drives the Addons page with
no local-rows** (the de-risking gate before fan-out).

### Stage 4 — remaining computed sources
Platform models/fields/edges; REBAC roles/grants/`rebac_schema`/`iam_overview`.
Each: a pydantic row model + `run_query` wrapping the existing fetch; preserve
admin gating in `run_query` (non-admin actor ⇒ empty/raise — decide per page).

### Stage 5 — native read-only resources for the queryset sources
`hasura_model_resource(... , get_queryset=scoped_admin_qs, insert=False,
update=False, delete=False)` for raw REBAC relationships
(`active_relationship_model()`) and resources ledger (`Resource`). Drop the
redundant authored `relationships`/`resourceLedger` queries.

### Stage 6 — delete the in-memory path (the payoff)
Remove `local-rows.ts` + `local-rows.test.ts`, `AuthoredRowsList`,
`useAuthoredRows` (if unused), `useRowsResourceViewSurface`. Collapse consumers
onto `useList`/`RelatedRowsList`/`ListView`. Update `docs/frontend/guidelines.md`
Pitfalls if a rule changes.

### Stage 7 — verify (Run From The Root)
`angee build` → `makemigrations` (expect none) → `migrate` → `rebac sync` →
`resources load` → `schema` + `schema --check`. `angee dev`; exercise platform
explorer, IAM roles/grants/relationships, resources console pages. `pytest`
(Angee + sibling) + `vitest`.

## Dependency check (stack.md)
- **pydantic**: confirmed transitive (2.13.4); promote "Proposed, Not Locked" →
  locked owner row + direct dep, same change. Concern: "typed row models for
  computed (non-Django-model) Hasura resources."
- **strawberry.experimental.pydantic**: confirmed available (strawberry 0.317.2).
- **strawberry-django-hasura**: bump pin to `>=0.3.0` when released.
- **queryish**: NOT added — `run_query` is the seam; queryish is an optional
  per-source implementation detail.

## Risks / open
1. Snake-case wire pinning on the pydantic-derived node — verify in Stage 1/2.
2. REBAC admin gating via `run_query` returns empty vs raises (current authored
   queries raise via `permission_classes`) — behavior change, confirm per page;
   navigation already gates the console.
3. Library must stay Angee/pydantic-agnostic — keep the builder generic (spec +
   run_query); pydantic derivation is Angee-side only.
4. Two-repo dev loop — temporary `[tool.uv.sources]` pin during dev; release +
   bump before merge.
5. Deletion accounting — net lines should drop once Stage 6 lands; track/report.

## Definition of done
Every former authored list query is a Hasura resource (native or
`hasura_pydantic_resource`); frontend uses `useList` uniformly; `local-rows.ts`
deleted; pydantic locked in `docs/stack.md` + manifest; sibling `0.3.0` released
and pinned; backend/frontend/schema checks green; `angee dev` console pages
verified.
