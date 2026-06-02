import type { CodegenConfig } from "@graphql-codegen/cli";

// Per-named-schema type generation. Each runtime endpoint prints its own SDL;
// codegen emits one types module per schema so a shell's call sites resolve
// filter / order / enum inputs against the schema it targets. Types only — urql
// generics plus the runtime document builder cover operations, so no per-op
// hooks are generated.
//
// Both named schemas read the one pinned contract today; it stands in for the
// runtime-printed SDL while the backend grows to serve it. When the endpoints
// diverge, point each output at its own `runtime/schemas/<name>.graphql` via a
// per-output `schema` override.
const scalars = {
  DateTime: "string",
  Date: "string",
  BigInt: "string",
  JSON: "unknown",
} as const;

const typescriptOutput = {
  plugins: ["typescript"],
  config: {
    scalars,
    enumsAsTypes: true,
    useTypeImports: true,
    skipTypename: true,
    avoidOptionals: false,
  },
};

const config: CodegenConfig = {
  schema: "schema/contract.graphql",
  generates: {
    "src/__generated__/public.ts": typescriptOutput,
    "src/__generated__/console.ts": typescriptOutput,
  },
};

export default config;
