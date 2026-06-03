import type { CodegenConfig } from "@graphql-codegen/cli";

// Types generated from the operator daemon's SDL. The daemon owns the schema;
// `OperatorDaemon.introspect_sdl()` refreshes `schema/operator.graphql` over the
// addon's existing authenticated daemon connection, and the console derives its
// types from it rather than hand-maintaining them. Types only — urql generics
// plus the document strings cover operations.
const config: CodegenConfig = {
  schema: "schema/operator.graphql",
  generates: {
    "src/__generated__/operator.ts": {
      plugins: ["typescript"],
      config: {
        scalars: { JSON: "unknown" },
        enumsAsTypes: true,
        useTypeImports: true,
        skipTypename: true,
        avoidOptionals: false,
      },
    },
  },
};

export default config;
