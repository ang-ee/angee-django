# Data Management Odoo-Parity Plan

**Date:** 2026-06-22
**Goal:** extract Angee's list/filter/group-by/aggregate/facet behavior into one
framework-owned data-management mechanism with matching backend and React pieces,
then migrate all existing addons to the shared contract. Start with the framework
core and the notes example.

This is a platform capability, not a product-addon feature. The plan should
produce less addon code over time: addons declare domain data intent, while
Angee emits the GraphQL contract, SDK metadata, query hooks, and rendered data
views.

## Implementation Status

First backend slice completed on 2026-06-22:

- [x] Use reproducible dependency sources: `strawberry` and
      `strawberry-django` resolve from Angee git branches, while
      `strawberry-django-aggregates>=0.6.0` resolves from the published
      package carrying the FK public-id echo seam.
- [x] Fix the aggregate-library FK filter-echo mismatch, publish it as
      `strawberry-django-aggregates==0.6.0`, and lock Angee to the published
      package.
- [x] Move Angee aggregate policy into `angee.graphql.data.aggregates`, leaving
      `angee.graphql.aggregates` as a compatibility export.
- [x] Add `data_query(...)`, a Strawberry-native query class builder that emits
      list, detail, aggregate, and grouped roots from the same declaration.
- [x] Refactor the notes example onto `data_query(...)`.
- [x] Refactor `agents.InferenceModel` onto `data_query(...)` while preserving
      the existing GraphQL root names and `InferenceModelAggregate*` SDL types.
- [x] Refactor the remaining aggregate-enabled addon schema surfaces onto
      `data_query(...)`: `integrate.Integration`, `parties.Party`,
      `parties.Handle`, `messaging.Message`, and `messaging.Thread`.
- [x] Add focused tests for the data-query builder and editable dependency
      import isolation.
- [x] Add backend-only `DataQueryMetadata` attached by `data_query(...)` and
      collected by `GraphQLSchemas.data_queries(schema_name)`.
- [x] Attach the same metadata to each built `AngeeSchema` as
      `schema.angee_data_queries`, with a JSON-safe copy under
      `schema._schema.extensions["angee"]["dataQueries"]`.
- [x] Verify the notes example exposes metadata for all migrated data surfaces:
      public = parties/handles/notes; console =
      integrate/agents/parties/handles/messaging/notes.

Second schema/SDK slice completed on 2026-06-22:

- [x] Add `GraphQLSchemas.render_metadata()` so schema-level Angee extensions
      have an artifact owner parallel to printed SDL.
- [x] Teach `GraphQLSdl` to emit/check/prune both
      `runtime/schemas/<name>.graphql` and
      `runtime/schemas/<name>.metadata.json`.
- [x] Generate deterministic metadata JSON shaped like
      `{"angee": {"dataQueries": [...]}}`.
- [x] Import the generated metadata artifacts in the notes host and pass them
      through each schema config beside the SDL.
- [x] Extend `@angee/sdk` model metadata to accept generated schema metadata and
      prefer it for data-query root names and relation public-id lookup axes.
- [x] Keep SDL inference as the compatibility fallback for tests, story fixtures,
      and non-Angee schemas.

Third frontend SDK source slice completed on 2026-06-22:

- [x] Add `packages/sdk/src/data/query.ts` as the first headless `DataQuery`
      owner for fields, filters, order, groups, measures, pagination, and
      selection identity.
- [x] Add `GraphQLDataSource` as the single SDK owner that maps data-query list,
      aggregate, and group requests to GraphQL documents and variables.
- [x] Refactor `useResourceList`, `useResourceAggregate`, and
      `useResourceGroupBy` so they delegate document/variable shape to the
      shared GraphQL data source.
- [x] Export the data-query/data-source surface from `@angee/sdk`.
- [x] Verify the notes example remains composed through `DataPage`, `List`,
      `GroupListView`, and `Form`; no note-local list data path was needed for
      this slice.
- [x] Move `DataViewState`, `Filter`, search serialization, favorites, and
      group/filter helper tests from `@angee/base` into `@angee/sdk`, leaving
      base's `data-view-model.ts` as a compatibility re-export.
- [x] Add `LocalRowsDataSource` in `@angee/sdk` for static/local row filtering,
      sorting, pagination, free-text search, and relation public-id matching.
- [x] Refactor `useRowsDataViewSurface` so `@angee/base` delegates local-row
      query mechanics to the SDK and keeps only presentation/table state.
- [x] Teach base filter/group option builders to consume generated
      `dataQuery.filterFields` and `dataQuery.groupByFields`, so model-backed
      pages such as notes inherit capability-driven toolbar options without
      local declarations for hidden filterable/groupable fields.
- [x] Teach base group-option derivation to turn relation display columns such as
      `party.displayName` into label-aware aggregate groups from relation
      metadata, and remove the handles page's local relation-group glue.

Fourth frontend facet slice completed on 2026-06-22:

- [x] Add SDK multi-facet support: one aliased grouped aggregate document can now
      fetch several facet buckets in one operation, with bucket counts and
      backend-echoed filters.
- [x] Add `useResourceFacets(...)` as the headless React owner for resource
      facet buckets, option value/label extraction, counts, and canonical
      server filter echoes.
- [x] Refactor base `useRelationFacet(...)` to prefer grouped backend facet
      buckets for relation filters and to fall back to related-row lists only
      when a schema lacks the relation group axis.
- [x] Keep relation facets label-aware by reusing the same
      id-axis + label-axis mapping as grouped lists (`provider` +
      `provider__name`, Odoo-style `(id, display_name)`).
- [x] Add `useRelationFacetsForColumns(...)` as the base hook for the next
      deletion slice, but keep it opt-in. A visible relation column should not
      automatically dump every related record into the toolbar's quick filters.

Fifth frontend auto-facet deletion slice completed on 2026-06-22:

- [x] Add an SDK `useGraphQLProviderAvailable()` hook so optional framework
      reads can stay inert in static/no-provider surfaces while required
      resource reads still use the normal GraphQL provider contract.
- [x] Keep `ListView` relation behavior group-first: visible relation columns
      contribute label-aware group options, but not automatic quick filters.
      High-cardinality relations such as contacts are a bad quick-filter UX.
- [x] Teach default relation groups to resolve through the same metadata path as
      toolbar group options, so pages can seed defaults with `provider.name`
      without repeating `aggregateField`/`aggregateKey`.
- [x] Delete folder relation-facet glue from the parties `PeoplePage`.
- [x] Delete provider relation-facet glue from the agents inference models page.
- [x] Delete sender/thread relation-facet glue from the messaging inbox; channel
      remains explicit because that relation is not visible as a column.

Sixth frontend declarative-facet slice completed on 2026-06-22:

- [x] Add a declarative `<Facet />` page element beside `<Column />`, parsed by
      the same cached page DSL and accepted by `List`/`DataPage`.
- [x] Teach `ListView` to resolve declared relation facets centrally through one
      model-level facet query and merge those filters/group options with the
      existing toolbar option owners.
- [x] Replace the messaging channel `useRelationFacet(...)` hook glue with
      `<Facet field="channel" label="Channel" labelField="displayName" />`.
- [x] Keep visible relation columns group-first only; quick filters remain
      explicit opt-ins for high-cardinality relations.
- [x] Preserve parent collection search params when opening routed detail/form
      records, so record Prev/Next walks the filtered/sorted parent query rather
      than losing context and showing only `/ total`.
- [x] Preserve grouped leaf query scope when opening a record from an expanded
      group, so detail/form Prev/Next walks that bucket's filter/page instead of
      the top-level group page or flat collection.
- [x] Promote `parties.Person` from a plain paginated root to a full
      `data_query(...)` surface while keeping the public `people` / `person`
      roots stable.
- [x] Switch the parties People page to `GroupListView` and make Folder an
      explicit relation facet, so the toolbar has useful filters/group-by
      without dumping every contact into quick filters.

Seventh frontend page-parity slice completed on 2026-06-22:

- [x] Switch the messaging Threads page to `GroupListView`; its backend
      `ThreadDataQuery` already owns filters/groups/aggregates.
- [x] Make thread Channel an explicit relation facet because it is a useful
      filter/group axis but is not displayed as a list column.

Eighth all-addon adoption slice completed on 2026-06-22:

- [x] Teach `data_query(...)` to forward list-root options such as custom
      resolvers, so generated data roots can keep resolver-owned querysets
      (`credential_health`, account health) without hand-written list fields.
- [x] Promote the remaining model-backed addon catalogue/admin pages onto
      `data_query(...)`: agents, skills, MCP servers/tools, inference providers,
      OAuth clients, external accounts, credential health, vendors, webhooks,
      VCS bridges, repositories, sources, templates, IAM users, storage
      drives/backends, knowledge vaults/pages, parties organizations, and
      directories.
- [x] Switch every top-level model-backed addon `<List>` to `GroupListView` and
      keep relation quick filters explicit and low-cardinality. Static daemon,
      introspection, graph, file-explorer, and detail-tab row surfaces remain on
      `RowsListView` because they are not top-level data-query pages.
- [x] Avoid nullable relation axes as default groups until the backend owner has
      explicit null-filter echo semantics; nullable relations remain ordinary
      form/list filters.
- [x] Add a focused regression test proving `data_query(...)` forwards a custom
      list resolver to the generated paginated root.

Ninth public-identity guardrail slice completed on 2026-06-22:

- [x] Add `AngeeDataModel` as the canonical sqid-backed base for concrete
      public data rows.
- [x] Make `data_query(...)` fail fast unless the model exposes an Angee sqid
      identity, with a named raw-pk compatibility flag for the IAM source-test
      auth.User fallback only.
- [x] Add `SqidProxyMixin` for Django proxy models, because Django forbids
      field-bearing abstract bases on proxies.
- [x] Expose Django auth `Group` through an IAM-owned proxy model with `grp_`
      public ids.
- [x] Promote auth groups to an IAM console `data_query(...)` surface and a
      React `DataPage`/`GroupListView` page under Users. Django auth
      permissions stay unexposed because REBAC owns authorization semantics.

Current local verification:

- [x] `uv run python -m pytest tests/test_aggregates.py -q`
- [x] `uv run python -m pytest tests/test_agents_graphql.py::test_inference_models_query_accepts_provider_sqid_filter tests/test_agents_graphql.py::test_inference_model_groups_aggregate_runs_for_provider_and_capability -q`
- [x] `uv run examples/notes-angee/manage.py test example.notes.tests.test_iam_graphql.IAMGraphQLTests example.notes.tests.test_word_count.NoteWordCountGraphQLTests --verbosity=2`
- [x] `uv run examples/notes-angee/manage.py schema --check`
- [x] `uv run examples/notes-angee/manage.py makemigrations --check --dry-run --verbosity=1`
- [x] `uv run ruff check angee/graphql/data angee/graphql/aggregates.py addons/angee/agents/schema.py examples/notes-angee/addons/example/notes/schema.py tests/test_aggregates.py tests/__init__.py`
- [x] `uv run python -m pytest tests/test_integrate_graphql.py::test_integration_groups_aggregate_runs_with_rebac_scope -q`
- [x] `uv run ruff check angee/graphql/data angee/graphql/aggregates.py addons/angee/agents/schema.py addons/angee/integrate/schema.py addons/angee/parties/schema.py addons/angee/messaging/schema.py examples/notes-angee/addons/example/notes/schema.py tests/test_aggregates.py tests/__init__.py`
- [x] `uv run python -m pytest tests/test_aggregates.py tests/test_integrate_graphql.py::test_console_data_query_metadata_declares_integration_surface tests/test_integrate_graphql.py::test_integration_groups_aggregate_runs_with_rebac_scope tests/test_agents_graphql.py::test_inference_model_groups_aggregate_runs_for_provider_and_capability -q`
- [x] `uv run examples/notes-angee/manage.py shell -c "... GraphQLSchemas.from_discovery().data_queries(...) ..."`
- [x] `uv run examples/notes-angee/manage.py shell -c "... GraphQLSchemas.from_discovery().build('console').angee_data_queries ..."`
- [x] `uv run python -m pytest tests/test_sdl.py tests/test_aggregates.py tests/test_integrate_graphql.py::test_console_data_query_metadata_declares_integration_surface -q`
- [x] `uv run ruff check angee/graphql/sdl.py angee/graphql/schema.py angee/graphql/data tests/test_sdl.py tests/test_aggregates.py tests/test_integrate_graphql.py`
- [x] `pnpm --filter @angee/sdk test -- model-metadata`
- [x] `uv run examples/notes-angee/manage.py schema`
- [x] `uv run examples/notes-angee/manage.py schema --check`
- [x] `pnpm --filter @angee/sdk typecheck`
- [x] `pnpm --filter @angee-example/notes-host typecheck`
- [x] `pnpm --filter @angee/sdk test -- graphql-source aggregates resource-hooks`
- [x] `pnpm --filter @angee/sdk test -- view-state graphql-source aggregates resource-hooks`
- [x] `pnpm --filter @angee/sdk test -- local-source view-state graphql-source aggregates resource-hooks`
- [x] `pnpm --filter @angee/base typecheck`
- [x] `pnpm --filter @angee/base test -- DataPage GroupedList ListView AggregatePanel`
- [x] `pnpm --filter @angee/base test -- RowsListView DataPage GroupedList ListView AggregatePanel`
- [x] `pnpm --filter @angee/base test -- model-metadata-defaults DataPage ListView RowsListView`
- [x] `pnpm --filter @angee/parties typecheck`
- [x] `pnpm --filter @angee/sdk test -- facets graphql-source aggregates resource-hooks`
- [x] `pnpm --filter @angee/sdk typecheck`
- [x] `pnpm --filter @angee/base test -- relation-facet`
- [x] `pnpm --filter @angee/base typecheck`
- [x] `pnpm --filter @angee/agents typecheck`
- [x] `pnpm --filter @angee/messaging typecheck`
- [x] `pnpm --filter @angee/parties typecheck`
- [x] `pnpm --filter @angee-example/notes-host typecheck`
- [x] `pnpm --filter @angee/base test -- relation-facet model-metadata-defaults DataPage DeleteBulkFlow`
- [x] `pnpm --filter @angee/base typecheck`
- [x] `pnpm --filter @angee/sdk typecheck`
- [x] `pnpm --filter @angee/agents typecheck`
- [x] `pnpm --filter @angee/messaging typecheck`
- [x] `pnpm --filter @angee/parties typecheck`
- [x] `pnpm --filter @angee-example/notes-host typecheck`
- [x] `pnpm --filter @angee/sdk test -- facets graphql-source aggregates resource-hooks`
- [x] `pnpm --filter @angee/base test -- page relation-facet DataPage`
- [x] `pnpm --filter @angee/base typecheck`
- [x] `pnpm --filter @angee/messaging typecheck`
- [x] `pnpm --filter @angee/base test -- DataPage.routed DataPage`
- [x] `pnpm --filter @angee/base typecheck`
- [x] `uv run python -m pytest tests/test_parties_graphql.py`
- [x] `uv run ruff check addons/angee/parties/schema.py tests/test_parties_graphql.py`
- [x] `uv run examples/notes-angee/manage.py schema --check`
- [x] `pnpm --filter @angee/parties typecheck`
- [x] `pnpm --filter @angee/messaging typecheck`
- [x] `pnpm --filter @angee/base test -- page relation-facet`
- [x] `uv run ruff format angee/graphql/data/queries.py addons/angee/agents/schema.py addons/angee/iam/schema.py addons/angee/integrate/schema.py addons/angee/knowledge/schema.py addons/angee/parties/schema.py addons/angee/storage/schema.py`
- [x] `uv run ruff check angee/graphql/data/queries.py addons/angee/agents/schema.py addons/angee/iam/schema.py addons/angee/integrate/schema.py addons/angee/knowledge/schema.py addons/angee/parties/schema.py addons/angee/storage/schema.py`
- [x] `uv run examples/notes-angee/manage.py schema`
- [x] `uv run examples/notes-angee/manage.py schema --check`
- [x] `pnpm codegen`
- [x] `pnpm --filter @angee/agents --filter @angee/integrate --filter @angee/iam --filter @angee/parties --filter @angee/storage --filter @angee/knowledge typecheck`
- [x] `uv run pytest tests/test_aggregates.py tests/test_integrate_graphql.py tests/test_agents_graphql.py tests/test_parties_graphql.py tests/test_iam_graphql.py tests/test_knowledge_graphql.py tests/test_storage.py`
- [x] `pnpm --filter @angee/base test -- src/views/relation-facet.test.tsx src/views/DataPage.test.tsx src/views/group-dimension.test.ts`

## North Star

Build Angee's equivalent of Odoo's domain/search/read-group stack while keeping
Angee's owners:

- Django and Strawberry own model/query/filter fundamentals.
- `strawberry-django-aggregates` owns SQL aggregate/group compilation.
- `angee.graphql` owns public GraphQL data contracts, REBAC safety, public-id
  filter echo, and schema metadata.
- `@angee/sdk` owns headless data-query state, SDL/capability metadata, GraphQL
  operation assembly, and transport adapters.
- `@angee/base` owns rendered data-view controls and views.
- Addons own only domain facts: model fields, labels, defaults, domain-specific
  actions, and rare presentation aliases.

## Architecture Gate

Owner map:

- **Filter/domain facts:** backend data contract plus SDK filter algebra.
- **Raw database ids:** Django/SQL internals only. Public filters use `sqid` or
  the model's public-id field. Aggregate bucket filter echo converts raw FK pks
  to public ids inside the backend data owner.
- **Row security:** model/queryset owner, via `AngeeQuerySet.scoped_for_aggregate`
  and normal REBAC-scoped list querysets.
- **Field-read security:** GraphQL access owner. Group axes and measures that can
  leak gated values fail contract validation.
- **Group/aggregate compilation:** `strawberry-django-aggregates`, wrapped by
  Angee rather than reimplemented.
- **URL/search state:** SDK data-query state, rendered by `@angee/base` controls.
- **Facets/search panel:** backend group/facet queries plus SDK data manager.
- **Saved views/favorites:** SDK/base data-view owner, not page-local storage.
- **Board lanes:** same data group contract as grouped lists.

Sibling inventory:

- Existing aggregate-enabled backend schemas: notes, integrate, agents,
  messaging, parties.
- Existing filter/order-only schemas: IAM, storage, knowledge, parts of
  integrate, resources, and other addon model lists.
- Existing frontend model-backed grouped pages: notes, integrations, inference
  models, messages, handles/people.
- Existing frontend static/daemon row pages: resources, IAM admin surfaces,
  operator/platform pages, storage browser surfaces.
- Existing Odoo-parity research note:
  `.agents/notes/grouped-list-odoo-parity.md`.
- Local library research note:
  `.agents/notes/data-management-library-research.md`.

Dependency check:

- Do not build a new SQL aggregate engine.
- Do not invent a second table state engine beside TanStack Table.
- Do not add a dependency without updating `docs/stack.md` and manifests
  together.

Thin caller check:

- Pages should not construct relation facets, group bucket filters, raw-id
  filters, aggregate GraphQL documents, search-panel counts, or local grouping
  semantics.
- Schema modules should not repeat filter/order/group/measure declarations in
  unrelated builder calls once the data contract exists.

Deletion check:

- Every phase must identify addon code that becomes unnecessary.
- Temporary line increases are acceptable only when the new owner unlocks
  deletion across multiple addons.

Naming check:

- Use one vocabulary across backend, GraphQL, SDK, and UI:
  `DataQuery`, `Filter`, `Order`, `Group`, `Measure`, `Facet`, `Bucket`,
  `Capability`.
- Prefer `public id`/`sqid` in public contracts. Avoid `pk` in frontend-facing
  names except in explicitly internal compatibility code.

## Hard Platform Opinions

The data-management layer should be opinionated enough that addons cannot drift
into alternate identities or local query semantics.

- [x] Every concrete/public Angee data row has an opaque public id, exposed as
      `sqid` at the GraphQL boundary.
- [x] Raw database primary keys are never public data filters, group keys, URL
      state, row ids, or addon-facing identifiers.
- [x] A model that can appear in a data contract must be sqid-addressable.
- [ ] A model-backed page must use the data contract and data source. A page-local
      table/filter/group implementation is a framework bug unless the surface is
      intentionally not model-backed.
- [ ] Relation filters always target the related model's public id.
- [ ] Group bucket drill-down filters are emitted by the backend. React never
      reconstructs raw database predicates from bucket keys.
- [ ] Sort, group, filter, and measure fields are explicit allow-lists. No
      "all readable fields" default for public data surfaces.
- [ ] Field paths that cross to-many relations are unsupported until the data
      owner has explicit semantics for count/existence/grouping and performance.
- [ ] Null semantics are explicit. `null` must not collapse to an unconstrained
      filter.
- [ ] Addons can add labels and defaults, but not alternate data-query machinery.

### Data Model Base

Introduce a hard public data-model contract, while preserving composition
semantics for extension models:

```py
class AngeeDataModel(SqidMixin, AngeeModel):
    """Base for concrete Angee rows that participate in public data contracts."""
```

Rules:

- [x] `AngeeDataModel` is the normal base for new concrete model roots.
- [ ] Existing normal models should migrate from `SqidMixin, ..., AngeeModel` to
      the cleaner `..., AngeeDataModel` shape where Django MRO allows it.
- [ ] Models with special inheritance, such as the swappable user model, may keep
      `SqidMixin` directly, but must satisfy the same structural public-id
      contract.
- [ ] Extension models with `extends = "app.Model"` do not declare a second sqid
      when they are folded into the parent row. They inherit the parent model's
      identity and public data contract unless they materialize as separate rows.
- [ ] Abstract behavior bases may inherit `AngeeModel` without `SqidMixin`, but
      they cannot be direct public data contracts.
- [ ] The composer should fail fast for a concrete runtime model root that is
      public/model-backed but not sqid-addressable.
- [ ] The data-contract builder should fail fast when `model.public_id_lookup`,
      `model.public_id_from_pk`, or a compatible `sqid` field is missing.
- [ ] Eventually remove `AngeeModel`'s fallback from public id to raw `pk`; keep
      any temporary fallback only in explicitly internal compatibility helpers.

Potential split:

- `AngeeModel`: composition, timestamp, REBAC manager/queryset, extension
  mechanics.
- `AngeeDataModel`: public identity, data-contract eligibility, default data
  queryset hooks, public row id.
- `DataQuery`: GraphQL/API exposure, filters, ordering, groups, measures,
  facets, metadata.

This keeps persisted identity on the model owner, while GraphQL exposure remains
owned by `angee.graphql.data`.

## Prior Art Beyond Odoo

Primary references checked during planning:

- Hasura GraphQL: generated table APIs use a single `where` boolean-expression
  shape with nested relation filters, explicit operators, and `_and`/`_or`; it
  also exposes generated aggregate fields for count/sum/avg/min/max.
  <https://hasura.io/docs/2.0/queries/postgres/filters/index/>
  <https://hasura.io/docs/2.0/queries/postgres/aggregation-queries/>
- Directus: one query grammar covers `fields`, `filter`, `search`, `sort`,
  pagination, `aggregate`, `groupBy`, deep nested queries, and date functions.
  <https://directus.com/docs/guides/connect/query-parameters>
- Django REST Framework: ordering/filter exposure should be explicit; the docs
  call out ordering all readable fields as a possible leakage risk.
  <https://www.django-rest-framework.org/api-guide/filtering/>
- Strapi: filter grammar has explicit comparison, null, membership, AND/OR/NOT,
  and relation/deep filters, plus a warning that deep filters can become a
  performance concern.
  <https://docs.strapi.io/cms/api/rest/filters>
- React-admin: list pages are resource-driven and route through a data provider;
  the view layer asks for list data with pagination/sort/filter rather than
  owning transport details.
  <https://marmelab.com/react-admin/List.html>
- TanStack Table: table state can be controlled by the application, while
  grouping/filtering row models are library-owned for local data.
  <https://tanstack.com/table/latest/docs/guide/column-filtering>
  <https://tanstack.com/table/latest/docs/guide/grouping>
- AG Grid server-side row model: grouping at scale is a server data-source
  protocol; requests carry grouping metadata and the server performs grouping.
  <https://www.ag-grid.com/react-data-grid/server-side-model-grouping/>

Resulting Angee design pressure:

- [ ] One declarative data grammar, not separate page-level grammars.
- [ ] Explicit allow-lists for exposed fields and operators.
- [ ] Server-side grouping/facets for model data.
- [ ] Client-side row models only for intentionally local/static data.
- [ ] Public identity belongs to the framework model base, not individual addon
      habits.
- [ ] Deep relation filters/grouping require explicit performance semantics.

## Odoo Parity Target

Parity means Angee can support the same data-management affordances, expressed
through Angee's stack:

- [ ] First-class domain/filter algebra with AND, OR, NOT, leaf removal, field
      path support, serialization, and client-side evaluation for local rows.
- [ ] Shared search/data model owning active filters, field searches, group-by,
      sort, pagination, facets, favorites, and view mode.
- [ ] Server-backed list queries with filter, order, pagination, selection, and
      stable row identity.
- [ ] Server-backed aggregate queries for count plus sum, avg, min, max,
      count-distinct, and supported dependency-native aggregate operators.
- [ ] Server-backed group queries with bucket key, display label, count,
      measures, drill-down filter, optional temporal range, and stable ordering.
- [ ] Lazy nested groups: group one axis at a time, expand a bucket to fetch the
      next axis or leaf records.
- [ ] Group-level pagination: top pager pages buckets, not records.
- [ ] Per-group record pagination: expanded groups fetch and page their own
      records independently.
- [ ] Fold/open state with Odoo-like defaults: groups folded by default, bounded
      auto-open support when requested.
- [ ] Date/datetime granularities with correct half-open ranges.
- [ ] Relation group labels as public-id plus display label, equivalent to
      Odoo's `(id, display_name)` behavior.
- [ ] Empty group expansion for declared axes, including enums/statuses and
      relation axes where a source can provide available values.
- [ ] Search panel/facet counts from the server, with the current facet's own
      filter neutralized when computing its options.
- [ ] Saved filters/views/favorites using the same `DataQuery` serialization.
- [ ] Board lanes backed by the same group contract as grouped lists.
- [ ] Local/static rows use the same query semantics through a local data-source
      adapter, not a separate implementation.
- [ ] Capability metadata exposes filterable, sortable, groupable, measurable,
      facetable, label, and granularity facts to React.
- [ ] REBAC and field-gating safety are enforced at the data contract boundary.
- [ ] Generated outputs and SDL are regenerated from source.

## Current Gaps

- [ ] Backend filter, order, group, and measure facts are still declared as
      Python schema types plus `data_query(...)` arguments; next slice should
      decide how much of that becomes model-owned metadata.
- [x] Aggregate/list/detail roots for existing aggregate-enabled addons now use
      the shared `data_query(...)` owner instead of manual builder wiring.
- [ ] Public row identity is scattered: many models use `SqidMixin`, but
      `AngeeModel` still has a raw-`pk` public-id fallback and some runtime
      models are not visibly sqid-backed.
- [ ] Frontend group/filter/facet options are partly inferred from columns,
      declarative facets, and some remaining addon group defaults.
- [x] Addon relation facets no longer use page hooks such as
      `useRelationFacet`; relation quick filters are declarative opt-ins.
- [ ] Selection options can be inferred from only the current page's rows.
- [ ] `RowsListView` has local filtering/sorting/grouping code that only mirrors
      a subset of server semantics.
- [ ] Board mode groups current page rows instead of server bucket lanes.
- [x] Data-query root and relation-axis discovery now has explicit generated
      schema metadata consumed by the SDK.
- [ ] Full capability discovery for facets, measures, operators, granularities,
      and saved-view defaults still needs a richer explicit contract.
- [ ] Raw `pk` compatibility leaks into frontend relation filter handling.
- [ ] Addon pages can still bypass the shared data-view owner and lose standard
      affordances.
- [x] Resource list, aggregate, and group hooks now share one SDK
      `GraphQLDataSource` owner for GraphQL document and variable shape.

## Target Package Shape

Backend, inside the framework first:

```text
angee/graphql/data/
  __init__.py
  contracts.py       # DataContract and axis specs
  filters.py         # filter/domain spec helpers
  aggregates.py      # wrapper over strawberry-django-aggregates
  facets.py          # facet/group-count helpers
  metadata.py        # GraphQL capability metadata
  validation.py      # REBAC/gated-axis/public-id checks
```

React/headless:

```text
packages/sdk/src/data/
  query.ts           # DataQuery, Filter algebra, serialization
  capabilities.ts    # generated/SDL-derived data capabilities
  source.ts          # data-source interfaces
  graphql-source.ts  # model-backed GraphQL adapter
  local-source.ts    # local/static row adapter
  groups.ts          # bucket drill-down and nested expansion model
  facets.ts          # server-backed facet option model
```

React/rendered:

```text
packages/base/src/views/data/
  DataToolbar.tsx
  ListView.tsx
  GroupListView.tsx
  BoardView.tsx
  FacetControls.tsx
  GroupRows.tsx
```

Exact filenames may change during implementation, but owner boundaries should
not.

## Data Contract Design

Working backend declaration shape:

```py
note_data = data_contract(
    model=Note,
    name="Note",
    filters=[...],
    orders=[...],
    groups=[...],
    measures=[...],
    facets=[...],
)
```

The contract must own:

- [ ] Model label and GraphQL naming.
- [ ] List root field and page shape, or compatibility with existing list root.
- [ ] Filter input type.
- [ ] Order input type.
- [ ] Aggregate root field.
- [ ] Group root field.
- [ ] Group key type.
- [ ] Group order input.
- [ ] Bucket filter echo shape.
- [ ] Measure operators per field.
- [ ] Group granularities per field.
- [ ] Relation axis public-id lookup and label path.
- [ ] Facet option query metadata.
- [ ] Capability metadata exported to frontend.

Contract validation:

- [ ] Every group axis must be filter-echoable unless explicitly declared as
      label-only.
- [ ] Direct FK bucket filters must echo public ids.
- [ ] Relation label axes must have a matching direct relation group axis.
- [ ] Gated read fields cannot be group axes or measures.
- [ ] Unsupported to-many paths fail fast.
- [ ] Null buckets must have a defined filter echo.
- [ ] Date buckets must have half-open range filters.
- [ ] Declared facets must have either enum values, group expansion values, or a
      relation option source.

Compatibility:

- [ ] Existing GraphQL root names for notes must remain stable during the first
      slice.
- [ ] Existing frontend `useResourceList`, `useResourceAggregate`, and
      `useResourceGroupBy` keep working while the new data source is introduced.
- [ ] Compatibility with existing SDL metadata stays until explicit capabilities
      are generated and consumed.

## SDK Data Query Design

`DataQuery` should own:

- [ ] `filter`
- [ ] `order`
- [ ] `groups`
- [ ] `measures`
- [ ] `page`
- [ ] `pageSize`
- [ ] opened group paths
- [ ] per-group pagination
- [ ] selected ids
- [ ] visible columns
- [ ] view mode
- [ ] favorite/saved-view identity

`Filter` algebra should support:

- [ ] AND, OR, NOT.
- [ ] Scalar comparisons: exact, not exact, contains, icontains, starts/ends
      with, gt, gte, lt, lte.
- [ ] Membership: in, not in.
- [ ] Null checks.
- [ ] Relation public-id lookup.
- [ ] Date/datetime range leaves.
- [ ] Leaf removal by field path for facet-neutralized counts.
- [ ] Serialization to URL/search state.
- [ ] Serialization to GraphQL filter variables.
- [ ] Client-side evaluation for local/static rows.

Data-source adapters:

- [ ] `GraphQLDataSource` for model-backed data.
- [ ] `LocalRowsDataSource` for in-memory rows, using the same query semantics.
- [ ] Shared result shapes for lists, aggregates, groups, facets, and errors.
- [ ] Stable loading/empty/error state contracts consumed by `@angee/base`.

## Rendered Data Views

List view:

- [ ] Flat list uses `DataQuery` and `GraphQLDataSource`.
- [ ] Column sorting writes `DataQuery.order`.
- [ ] Toolbar filters write `DataQuery.filter`.
- [ ] Pagination writes `DataQuery.page`.
- [ ] Selection uses stable public ids.

Grouped list:

- [ ] Group toolbar writes `DataQuery.groups`.
- [ ] Top pager pages group buckets.
- [ ] Group rows render count and measures.
- [ ] Expanding a group composes parent filter plus bucket filter.
- [ ] Expanded leaf records fetch through the same list source.
- [ ] Nested groups fetch one level at a time.
- [ ] Per-group pagers are independent.
- [ ] Fold state is URL-safe where useful and local where it would be too noisy.

Board:

- [ ] Model-backed lanes come from group buckets.
- [ ] Lane counts/measures use the same group result shape.
- [ ] Lane cards fetch per-lane records through bucket filters.
- [ ] Static row boards use `LocalRowsDataSource`.

Facets/search panel:

- [ ] Enum/status facets come from server group counts.
- [ ] Relation facets come from server group counts plus labels.
- [ ] Date facets can use granularity buckets.
- [ ] Each facet count neutralizes that facet's own filter.
- [ ] Addons no longer hand-merge facet options except for true domain-specific
      presentation aliases.

## Phase 0: Groundwork

- [ ] Re-read `docs/stack.md`, `docs/guidelines.md`,
      `docs/backend/guidelines.md`, and `docs/frontend/guidelines.md` before
      implementation.
- [ ] Re-read `.agents/notes/grouped-list-odoo-parity.md`.
- [ ] Capture exact current notes GraphQL SDL before changing the schema.
- [ ] Capture current notes list/group UI behavior before changing React.
- [ ] Add a small glossary entry or doc note for "data contract" once the API is
      real.

## Phase 1: Backend Framework Contract

- [ ] Create `angee/graphql/data/` package.
- [ ] Move or wrap `AngeeAggregateBuilder` and `rebac_aggregate_builder` behind
      the new data owner without breaking existing imports.
- [ ] Define spec dataclasses for filters, orders, groups, measures, facets.
- [ ] Add validation for gated axes, relation label axes, public-id echo, null
      bucket echo, and unsupported paths.
- [ ] Generate aggregate and group roots from the contract.
- [ ] Generate or expose filter/order input types from the contract, initially
      allowing explicit existing types for compatibility.
- [ ] Emit capability metadata for the frontend.
- [x] Emit initial data-query metadata for roots, type names, allow-lists,
      capabilities, and relation public-id axes.
- [ ] Add backend tests for contract validation and generated roots.
- [ ] Add backend tests proving FK group filters echo `sqid`, never raw pk.
- [ ] Add backend tests proving row scoping applies to aggregate/group queries.

Done when:

- [ ] Existing aggregate-enabled schemas can keep using the old helper.
- [ ] New notes contract can emit the same public schema shape.
- [ ] Tests prove raw IDs stay internal.

## Phase 1A: Data Model Contract Refactor

This phase can run before or alongside the backend data contract, but it should
land before broad addon migration.

- [ ] Add `AngeeDataModel` or an equivalent hard public-data base.
- [ ] Decide whether `AngeeDataModel` lives in `angee.base.models` or a narrower
      `angee.base.data` module. Prefer the owner that keeps model identity near
      `AngeeModel`.
- [ ] Add composer validation for concrete runtime model roots without sqids.
- [ ] Add data-contract validation for models without public-id support.
- [ ] Inventory current `AngeeModel` subclasses that do not inherit `SqidMixin`.
- [ ] Classify each as one of: public data root, extension folded into parent,
      abstract behavior base, internal non-data table, or test-only model.
- [ ] Migrate public data roots to `AngeeDataModel` or `SqidMixin`.
- [ ] Keep extension-only models identity-free when the parent owns identity.
- [ ] Replace public-id fallback-to-pk behavior with fail-fast behavior in public
      data paths.
- [ ] Keep a temporary internal helper for old tests/import resources only if
      removing it immediately is too disruptive.
- [ ] Add tests for composer failure, data-contract failure, public-id lookup,
      and aggregate bucket echo.

Done when:

- [ ] A public data contract cannot be created for a model that lacks sqid/public
      id support.
- [ ] New addon examples show `AngeeDataModel` as the boring default.
- [ ] Any remaining non-sqid runtime model is documented as extension-only or
      intentionally not a data contract.

## Phase 2: SDK Data Core

- [x] Add `packages/sdk/src/data/query.ts`.
- [x] Move or mirror `DataViewState` and `Filter` into SDK-owned types.
- [ ] Implement URL serialization compatible with current search params.
- [ ] Implement GraphQL filter variable serialization.
- [x] Implement client-side evaluation for local rows.
- [ ] Add capability metadata reader.
- [x] Add `GraphQLDataSource` wrapping current resource hooks/document builders.
- [x] Add `LocalRowsDataSource` replacing `RowsListView` bespoke semantics.
- [x] Add unit tests for GraphQL data-source document/variable mapping and
      pagination policy.
- [x] Add unit tests for local row filtering, sorting, paging, text search, and
      public-id relation lookup.
- [ ] Add unit tests for filter algebra and query serialization.
- [ ] Add parity tests comparing local and GraphQL-style filtering semantics.

Done when:

- [ ] `@angee/base` can consume SDK data query types.
- [ ] No new page-level query/filter state APIs are needed.

## Phase 3: Notes First Slice

Backend:

- [ ] Replace notes manual aggregate builder with a notes `DataContract`.
- [ ] Preserve `note_aggregate` and `note_groups` public GraphQL roots.
- [ ] Preserve existing `NoteFilter` and `NoteOrder` names initially.
- [ ] Declare note groups: `status`, `updated_at` granularities.
- [ ] Declare note measures: `id` count and `word_count` numeric measures.
- [ ] Declare note facets: status, starred, updated date, and text search owner
      if/when text search is part of the contract.
- [ ] Regenerate runtime and SDL from source.
- [ ] Run notes backend/schema tests.

Frontend:

- [x] Wire notes page to capability-driven group/filter options.
- [ ] Wire notes page to server-backed facet options/counts.
- [ ] Keep notes page on `GroupListView`.
- [ ] Remove note-local group/filter declarations that metadata can supply.
- [ ] Verify grouped list: status groups, updated date groups, word-count
      measures, bucket drill-down, flat list fallback.
- [ ] Verify URL search state compatibility.

Done when:

- [ ] Notes behavior is unchanged or deliberately improved.
- [ ] The notes page has less manual data-view wiring.
- [ ] SDL and tests prove the framework contract owns the behavior.

## Phase 4: Grouped List Parity

- [ ] Normalize group bucket result shape in SDK: key, label, count, measures,
      filter, range, path.
- [ ] Make group expansion use bucket filter echo exclusively.
- [ ] Add explicit fold/open state model.
- [ ] Add group-level pagination UI.
- [ ] Add per-group record pagination UI.
- [ ] Add nested group expansion tests.
- [ ] Add date range filter echo tests.
- [ ] Add relation label group tests.
- [ ] Add empty enum/status group support.
- [ ] Decide whether relation empty groups need backend `group_expand`-style
      sources in the first milestone or a later milestone.

Done when:

- [ ] Grouped list can match Odoo's lazy group behavior for scalar, enum,
      relation, and date axes.

## Phase 5: Server-Backed Facets

- [x] Add backend facet query support using grouped aggregates.
- [x] Add SDK facet hooks/data-source calls.
- [ ] Implement facet-neutralized filters.
- [ ] Replace page-row-derived selection options.
- [x] Replace most `useRelationFacet` usage with metadata-driven facets.
- [x] Support relation label display and public-id filters.
- [ ] Support facet search for large relation sets.
- [ ] Add tests for enum, relation, date, and boolean facets.

Done when:

- [ ] Facet options and counts are correct for the whole filtered result set, not
      just the current page.

## Phase 6: Board Parity

- [ ] Make model-backed board lanes use server group buckets.
- [ ] Fetch lane records with bucket filters.
- [ ] Add per-lane paging or bounded loading.
- [ ] Render lane counts consistently with grouped list counts.
- [ ] Reuse group labels and relation display labels.
- [ ] Keep static row board backed by `LocalRowsDataSource`.
- [ ] Add board tests for model-backed and local-row data.

Done when:

- [ ] Board and grouped list agree on lane/group counts and filters.

## Phase 7: Saved Views And Search Model

- [ ] Promote favorites/saved data-view state into SDK data query storage.
- [ ] Define saved-view schema: name, model, query, visible columns, view mode.
- [ ] Keep local storage first if no backend persistence owner exists.
- [ ] Add migration path from current `angee:data-view:*:favorites` keys.
- [ ] Add import/export or backend persistence only after owner approval.
- [ ] Add tests for saved filters, group defaults, and view mode restore.

Done when:

- [ ] Saved views round-trip the same data query used by list/group/board/facets.

## Phase 8: Addon Migration Queue

Migrate in dependency and risk order. Each migration should delete hand-wiring
that the data contract now owns.

### Notes

- [ ] Backend `Note` data contract.
- [ ] Frontend notes page capability-driven toolbar/groups.
- [ ] Notes tests and SDL check.

### Integrate

- [ ] `Integration` data contract for vendor, impl class/category, status.
- [ ] Express `implCategory` display alias as contract metadata instead of page
      group-option glue.
- [ ] Express `vendorLabel`/vendor relation or enum label mapping in the contract.
- [ ] Migrate `OAuthClient`, `ExternalAccount`, `Credential`, `Source`, and other
      list roots from filter/order-only to data contracts where useful.
- [ ] Delete manual integration group option wiring.

### Agents

- [ ] `InferenceModel` data contract for provider, publisher, model use, status,
      context window, max output tokens.
- [x] Replace provider relation facet hook with metadata-driven relation facet.
- [ ] Add contracts for agent catalogue/list surfaces where model-backed.
- [ ] Verify agent pages keep shared list/group/board affordances.

### Messaging

- [ ] `Message` data contract for thread, sender, channel, status, platform,
      direction, sent date.
- [ ] `Thread` data contract for channel, modality, visibility, last message date.
- [x] Replace sender/channel/thread `useRelationFacet` hand-wiring.
- [ ] Verify nested groups and relation labels.

### Parties

- [ ] `Party` data contract for created date, handle counts, folders where
      applicable.
- [ ] `Handle` data contract for party relation plus display label.
- [x] Replace party relation facet wiring.
- [ ] Verify null-party handling remains explicit and safe.

### Storage

- [ ] Add contracts for `Backend`, `Drive`, `Folder`, `File`, and upload/list
      surfaces where they are model-backed.
- [ ] Decide which file-browser surfaces are true model data views and which are
      bespoke tree/browser UX.
- [ ] Move model-backed filters/groups to the contract.
- [ ] Keep file-browser path/tree behavior in the storage owner.

### Knowledge

- [ ] Add contracts for `Vault`, `Page`, markdown page/link list surfaces.
- [ ] Verify parent/page relation filters use public ids.
- [ ] Add date/status/type facets where useful.

### IAM

- [ ] Add contracts for users where model-backed and permission-safe.
- [ ] Audit grants/relationships/roles for model-backed vs static/derived rows.
- [ ] Use local data source for derived rows only when there is no model source.
- [ ] Keep REBAC-specific graph behavior in IAM backend owners.

### Resources

- [ ] Identify static resource rows vs persisted resource models.
- [ ] Replace local row filtering with `LocalRowsDataSource`.
- [ ] Add contracts only for persisted resource surfaces.

### Operator And Platform

- [ ] Classify daemon/platform rows as local data sources unless persisted models
      own them.
- [ ] Replace hand-written filtering/grouping with local data-source semantics.
- [ ] Do not invent backend contracts for daemon data without a persisted owner.

### OIDC And Provider Addons

- [ ] Verify they inherit contracts from IAM/integrate where possible.
- [ ] Add provider-specific facets only when the provider owns a distinct field.
- [ ] Avoid duplicating base addon data declarations.

Done when:

- [ ] Every model-backed addon list either uses a data contract or documents why
      it is intentionally outside the generic data-management surface.
- [ ] Every static/daemon row view uses the local data source rather than bespoke
      filter/group code.
- [ ] Addon code for relation facets and group options is materially reduced.

## Phase 9: Documentation And Guardrails

- [ ] Update `docs/stack.md` to name the new data contract owner.
- [ ] Update backend guidelines with data contract rules.
- [ ] Update frontend guidelines with data-query/data-source rules.
- [ ] Update model guidelines to require sqids/public ids for public data models.
- [ ] Add a how-to for adding a model-backed data page.
- [ ] Add a how-to for relation facets and group labels.
- [ ] Add architecture tests or grep checks that catch page-level relation facet
      hand-wiring when metadata can own it.
- [ ] Add tests that prevent public `pk` filters from appearing in frontend
      capability metadata.
- [ ] Add import-graph checks preserving the lean `ListView` versus
      `GroupListView` separation.

Done when:

- [ ] A new addon can follow docs and get list, filter, group, aggregate, facets,
      board, and saved views without hand-rolling data mechanics.

## Verification Matrix

Backend:

- [ ] `uv run examples/notes-angee/manage.py angee build`
- [ ] `uv run examples/notes-angee/manage.py schema`
- [ ] `uv run examples/notes-angee/manage.py schema --check`
- [ ] Focused backend tests for `angee.graphql.data`.
- [ ] Existing aggregate tests.
- [ ] Notes schema/list/group tests.

Frontend:

- [ ] SDK unit tests for `DataQuery`, filters, capabilities, GraphQL source, local
      source.
- [ ] Base tests for ListView, GroupListView, BoardView, DataToolbar, facets.
- [ ] Browser/e2e verification for notes list/group/board.
- [ ] Visual/screenshot checks for grouped list and board if layout changes.

Cross-cutting:

- [ ] SDL diff reviewed.
- [ ] Generated runtime diff reviewed.
- [ ] No generated file edited by hand.
- [ ] Addon migration diff reports net deleted hand-wiring.

## Open Decisions

- [ ] Should the backend contract generate Strawberry filter/order types itself,
      or consume explicit types for the first release and generate later?
- [ ] Should capability metadata live as GraphQL fields, directives, or a
      dedicated metadata query?
- [ ] Should `AngeeModel` itself eventually include `SqidMixin`, or should
      `AngeeDataModel` be the public-data base while `AngeeModel` stays the
      composition base for extensions and internal tables?
- [ ] Should saved views remain local-storage first or get backend persistence
      before all addon migrations?
- [ ] How much Odoo `group_expand` parity is required in milestone one for
      relation axes?
- [ ] Should `@angee/sdk` keep all headless data code, or should a later
      `@angee/data` package split out query algebra and local data-source logic?
- [ ] Should group open/fold state be URL state, local state, or hybrid?

## Upstream Library Candidates

Move behavior upstream when Angee glue is compensating for a generic library gap.
Keep behavior in Angee when it encodes Angee policy, such as REBAC, public ids,
composition, or addon contracts.

- [ ] `django-sqids`: investigate whether Angee's NULL-safe decode behavior in
      `SqidField.from_db_value` belongs upstream. If accepted upstream, delete the
      wrapper code that exists only for total decode behavior.
- [ ] `strawberry-django-aggregates`: investigate first-class bucket filter/domain
      echo, public hook points for FK value conversion, relation label axes,
      temporal range echo, and group expansion. If upstream can own generic
      bucket-domain mechanics, keep only Angee public-id/REBAC policy in Angee.
- [ ] `strawberry-django`: investigate whether richer filter/order/group metadata
      can be exposed through stable schema metadata instead of Angee inferring
      type structure from SDL.
- [ ] TanStack Table: do not upstream Angee policy. Use its controlled state,
      grouping, filtering, selection, and row-model APIs as intended.
- [ ] React-admin/AG Grid ideas are prior art only; do not add either dependency
      unless `docs/stack.md` changes with a deliberate owner swap.

Decision rule:

- [ ] If the behavior is public-id conversion, REBAC scoping, composer
      composition, or addon-level data contracts, keep it in Angee.
- [ ] If the behavior is generic aggregate bucket domain echo, temporal range
      echo, NULL-safe sqid decoding, or generic metadata shape, open an upstream
      issue/PR before cementing a large Angee workaround.

## Immediate Next TODOs

- [ ] Implement Phase 1 in a compatibility-preserving way.
- [ ] Implement Phase 1A model contract validation or decide its exact slice
      ordering.
- [x] Migrate notes backend to the initial `data_query(...)` contract surface.
- [x] Implement the minimum SDK metadata reader needed by notes.
- [x] Add the first SDK `DataQuery`/`GraphQLDataSource` owner and route the
      existing list/aggregate/group hooks through it.
- [x] Promote base `DataViewState` and filter algebra into the SDK `DataQuery`
      owner.
- [x] Implement `LocalRowsDataSource` so static/daemon row views stop carrying a
      separate filter/sort/group implementation.
- [x] Migrate notes frontend to capability-driven groups/filter fields where
      generated metadata can own them.
- [ ] Migrate notes frontend to server-backed facets once facet metadata/source
      exists.
- [ ] Verify notes end to end.
- [ ] Review the first slice with this question: did the framework owner get
      stronger and did notes get thinner?
