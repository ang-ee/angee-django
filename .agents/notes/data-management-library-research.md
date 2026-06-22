# Data Management Library Research

Date: 2026-06-22

Scope:

- `../strawberry-django-aggregates`
- `../strawberry-django`
- Local Angee integration points that already touch their contracts.

## Executive Read

The aggregation wheel mostly already exists in `strawberry-django-aggregates`.
Use it as the backend owner for aggregate, group-by, having, grouped ordering,
bucket ranges, and grouped filter echo. Do not reimplement those mechanics in
Angee.

The filter and ordering owner is `strawberry-django`. It already supports nested
relationship filters, lookup objects, boolean composition, ordering input types,
and a configurable public pk field name through
`STRAWBERRY_DJANGO.DEFAULT_PK_FIELD_NAME`. Angee sets that setting to `sqid`.

The main upstream mismatch was filter echo for direct foreign-key group axes:
older `strawberry-django-aggregates` hardcoded `{relation: {pk: value}}`, while
`strawberry-django` says the lookup field is whatever `DEFAULT_PK_FIELD_NAME`
says. `strawberry-django-aggregates>=0.6.0` now fixes that by using the
configured lookup and by adding `filter_echo_relation_identity` so Angee can
convert grouped raw pks into public `sqid` values without overriding the whole
FK echo path.

## Architecture Gate

Owner map:

- Aggregate execution: `strawberry-django-aggregates.compute_aggregation`.
- GraphQL aggregate/grouped SDL generation: `strawberry-django-aggregates.AggregateBuilder`.
- Filter and order semantics: `strawberry-django` filter/order types and apply
  functions.
- Public model identity: Angee `SqidMixin`, `SqidField`,
  `angee.graphql.constants.PUBLIC_ID_FIELD_NAME`, and
  `STRAWBERRY_DJANGO.DEFAULT_PK_FIELD_NAME`.
- Permission scoping and gated fields: Angee GraphQL/REBAC.
- Frontend data-view mechanics: Angee SDK/base data-view primitives, backed by
  GraphQL metadata, not page-local inference.

Sibling inventory:

- `strawberry-django-aggregates` already has the reusable backend primitive and
  builder surface.
- `strawberry-django` already has the filter/order recursive machinery.
- Angee already has a narrow subclass in `angee/graphql/aggregates.py` and
  frontend compatibility fallbacks in `packages/sdk/src/model-metadata.tsx` and
  `packages/base/src/views/data-view-model.ts`.

Dependency check:

- `docs/stack.md` names `django-sqids` as the opaque external-id owner.
- The data-management extraction should stay glue over the locked libraries,
  not a replacement.

Thin caller check:

- Addons should declare model data contracts and compose list/group/aggregate
  primitives.
- Routes/pages should not construct GraphQL filter shape from raw local rules.

Deletion check:

- Moving the identity echo seam upstream or into a shared Angee data contract
  should remove Angee-only aggregate subclass logic and frontend `pk` fallbacks
  from normal paths.

Naming check:

- Public id is `sqid` at the GraphQL boundary.
- Raw `pk`/`id` remains internal Django/SQL vocabulary.

## strawberry-django-aggregates

Useful existing ownership:

- Package: `strawberry-django-aggregates` version `0.6.0`.
- Describes itself as "Hasura-shape aggregations over Django querysets in
  Strawberry GraphQL".
- Implements count, count distinct, sum, average, min, max, stddev, variance,
  boolean aggregates, array aggregates, string aggregates, grouped aggregate
  results, grouped ordering, having, and temporal bucketing.
- `compute_aggregation` is a pure backend primitive over Django querysets.
- `AggregateBuilder(...).build()` creates aggregate and group-by fields for
  Strawberry GraphQL.
- `filter_type` integration uses `strawberry_django.filters.apply`, so list
  filters and aggregate filters share the same upstream filter owner.
- `get_queryset(info)` exists as the host permission scoping hook. This is where
  Angee should pass a REBAC-scoped queryset.
- `enable_filter_echo=True` adds `filter: JSON!` on grouped buckets, shaped like
  the list query's existing `filter:` argument.
- Temporal group keys already emit half-open bucket ranges and use `gte`/`lt`
  echo filters.
- JSON path grouping is explicitly allowlisted and refuses filter echo where no
  faithful list-filter field exists.
- Forward to-one relation scalar group axes are supported; to-many group axes
  are refused to avoid row multiplication.
- Measures across to-many relations are refused by default, with explicit
  opt-in for subquery-isolated relation traversal.
- `respect_comodel_ordering=True` can append related model ordering for foreign
  key group ordering.

Important constraints:

- Grouped output is flat. Multi-axis grouping returns composite keys; recursive
  subgroup loading is a client/server repeated-call concern.
- `fill=True` currently supports exactly one time-granularity axis.
- Relation aggregate attachment exists through `register_relation_aggregate`,
  but it runs per parent row and has an N+1 caveat.

The identity seam:

- Direct foreign-key echo emits `{field: {<public-pk>: value}}`, where
  `<public-pk>` comes from `strawberry_django_settings()["DEFAULT_PK_FIELD_NAME"]`.
- `AggregateBuilder(filter_echo_relation_identity=...)` can convert the grouped
  raw related model pk to a public id and return lookup values such as
  `{sqid: "ven_..."}`.
- Tests cover default `pk`, configured `sqid`, and custom conversion.
- Angee locks the published aggregate package and only passes the public-id
  conversion hook from `angee.graphql.data.aggregates`.

Upstream PR shape:

- Replace the hardcoded relation filter lookup with the configured
  `strawberry-django` public pk field name, defaulting to current behavior:
  `strawberry_django_settings()["DEFAULT_PK_FIELD_NAME"]`.
- Add an explicit `filter_echo_relation_identity` strategy hook to
  `AggregateBuilder` that receives the relation field and raw grouped value,
  then returns lookup values.
- Add tests proving default `pk`, configured `sqid`, and custom conversion.

Second upstream candidate:

- Promote the current Angee "label-only relation axis" behavior into a first
  class option. Angee groups by a direct FK axis for drill-down and may include a
  related display-name axis only to label the bucket. The aggregate library
  currently refuses to-one axes for filter echo because the flat axis cannot
  map faithfully to the nested relation filter. The useful generic rule is:
  label-only axes may be omitted from echo when a direct relation identity axis
  for the same relation is present.

Keep in Angee:

- REBAC scoping.
- Gated field rejection.
- Public-id conversion if upstream does not accept the strategy quickly.
- Data contract metadata that tells React which axes are identity axes, label
  axes, measure axes, default order axes, and allowed operators.

## strawberry-django

Useful existing ownership:

- Package: `strawberry-graphql-django` version `0.86.3` from the Angee
  `codex/input-object-extensions` git branch.
- `strawberry_django.settings.DEFAULT_PK_FIELD_NAME` defaults to `pk`.
- `get_django_model_filter_input_type()` creates `DjangoModelFilterInput` using
  `DEFAULT_PK_FIELD_NAME`.
- `filters.apply(..., pk=...)` filters via the same configured field name.
- Relationship filters are nested input objects, not flattened strings.
- `@strawberry_django.filter_type(..., lookups=True)` owns lookup generation.
- Filters support `AND`, `OR`, `NOT`, and `DISTINCT`.
- `@strawberry_django.order_type` owns ordering input generation, including
  nested relation ordering and multiple-field order lists.
- Internally, `StrawberryDjangoDefinition` hangs metadata off generated types in
  `__strawberry_django_definition__`.

Confirmed Angee alignment:

- `angee/graphql/autoconfig.py` sets
  `STRAWBERRY_DJANGO.DEFAULT_PK_FIELD_NAME = "sqid"`.
- `tests/test_settings.py` asserts the composed GraphQL filter pk name is
  `sqid`.
- Existing GraphQL tests already filter relations by `{relation: {sqid: ...}}`.

Frontend implication:

- The Angee SDK currently infers relation filter shape from SDL and accepts
  `sqid`, `exact`, `pk`, and `inList` as compatibility paths.
- The extracted data-management layer should make the normal path explicit:
  relation filters use `sqid` for model identity. `pk` can remain a legacy or
  non-Angee compatibility fallback, but addon code should not rely on it.

Possible upstream ask:

- Expose a stable helper for "the public identity lookup field for a related
  model filter" so companion libraries do not inspect or hardcode filter input
  internals.
- Expose stable metadata helpers for generated filter/order fields. Backend code
  can read Python definitions today, but a formal helper would reduce private
  introspection in libraries like `strawberry-django-aggregates`.

Keep in Angee:

- Frontend GraphQL metadata query or generated metadata artifact. The browser
  cannot use Python-side `StrawberryDjangoDefinition`; it needs an Angee-owned
  GraphQL or generated contract for filters, group axes, aggregate fields, and
  relation labels.

## Angee Extraction Guidance

Use upstream as follows:

- Build backend aggregate/group APIs with `AggregateBuilder`.
- Always pass `filter_type`, `get_queryset`, `enable_filter_echo=True`, and
  Angee group/aggregate allowlists from a model-owned data contract.
- Keep list filtering/order semantics in strawberry-django input types.
- Treat bucket `filter` as the canonical drill-down contract. Do not let React
  invent raw-id filters from grouped rows.
- Use `sqid` as the only public relation identity lookup in Angee data
  contracts.
- Let the frontend compose from metadata and echoed filters; keep `pk` fallback
  only as a compatibility bridge until the backend cannot emit it in Angee mode.

Extraction priority:

1. Upstream or encapsulate relation identity echo so Angee no longer subclasses
   private aggregate internals for `sqid`.
2. Define an Angee data contract owner around a model/root field that supplies
   filter type, order type, group axes, measures, relation labels, defaults, and
   queryset scoping.
3. Generate or expose frontend data metadata from that same contract.
4. Refactor addons to compose the shared data-view primitive and delete local
   filter/group/aggregate construction.
