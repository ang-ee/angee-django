# @angee/metadata

`@angee/metadata` provides the schema-independent resource metadata and projection layer; it is a leaf package with no dependency on other Angee React packages, keeping metadata reusable beneath UI and application composition.

Install: `pnpm add @angee/metadata`

Resource indexes retain the parsed resource contract. `ResourceQuery.from(resource)`
resolves its executable filter, order, selection, and grouping semantics. Local
collections use `ResourceQuery.forRows` with explicit field declarations. Query
validation reports a `QueryParseError`; callers must surface it before issuing
dependent reads. Relation identity and display paths are separate query facts.

Operation roots, GraphQL node names, and record representations live on
`model.resource`. Relation targets use canonical `relationModelLabel` values.
`useModelRootFields`
returns the parsed `resource.roots` object, including its native nullable root
values; it still returns `null` without metadata, `undefined` for an optional
missing model, and throws for a required missing model.

Plain Node tools import `@angee/metadata/headless` to reuse the same Valibot
parser, reference indexes, and relation-selection semantics without importing
React. The wire owner is [`src/artifact-schema.ts`](src/artifact-schema.ts); the
headless index/selection owner is [`src/artifact.ts`](src/artifact.ts). Query wire
and runtime owners are [`src/query-schema.ts`](src/query-schema.ts) and
[`src/query.ts`](src/query.ts).

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/metadata/)
