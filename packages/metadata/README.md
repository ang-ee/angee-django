# @angee/metadata

`@angee/metadata` provides the schema-independent resource metadata and projection layer; it is a leaf package with no dependency on other Angee React packages, keeping metadata reusable beneath UI and application composition.

Install: `pnpm add @angee/metadata`

Resource indexes keep references to the parsed `angee.resources` contract. Code
migrating from the former copied model graph reads operation roots, GraphQL node
names, and record representations from `model.resource.roots`,
`model.resource.typeNames`, and `model.resource.recordRepresentation`. Relation
targets are canonical `relationModelLabel` values (or the owning resource's
relation axis), rather than model-name-derived GraphQL types. `useModelRootFields`
returns the parsed `resource.roots` object, including its native nullable root
values; it still returns `null` without metadata, `undefined` for an optional
missing model, and throws for a required missing model.

Plain Node tools import `@angee/metadata/headless` to reuse the same Valibot
parser, reference indexes, and relation-selection semantics without importing
React. The wire owner is [`src/artifact-schema.ts`](src/artifact-schema.ts); the
headless index/selection owner is [`src/artifact.ts`](src/artifact.ts).

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/metadata/)
