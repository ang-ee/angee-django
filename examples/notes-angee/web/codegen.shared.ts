import type { CodegenConfig } from "@graphql-codegen/cli";

// Per-schema client-preset codegen for one composed Angee project. Each named
// runtime schema (`public`, `console`) prints its own SDL into
// `runtime/schemas/<name>.graphql`; this emits a matching typed `graphql()`
// document factory + operation/scalar/enum types into `runtime/gql/<name>/`.
//
// Schema routing is BY DOCUMENT FILE. client-preset scans the `graphql(...)`
// identifier globally and cannot isolate two schemas by import module (its
// preset ignores `pluckConfig.modules`, and a custom `gqlTagName` is not
// plucked at all), so each run scans a disjoint, schema-pure set of files:
//
//   documents.ts / documents.console.ts  ->  console  (the default)
//   documents.public.ts                  ->  public
//
// An operation file therefore targets exactly one schema; an addon that uses
// both (e.g. iam login vs admin) keeps a `documents.public.ts` beside its
// `documents.ts`. Each file still imports `graphql` from `@angee/gql/<name>`
// for its types. The schema is the *generated* SDL (the one source of truth the
// app runs against), never a hand-maintained copy. Run after SDL emission.

const scalars = {
  DateTime: "string",
  Date: "string",
  BigInt: "string",
  JSON: "unknown",
} as const;

// Roots that author operations for the notes example project: framework addon
// packages in this monorepo, this project's consumer addons, and the app shell
// itself. Another project owns its own web-package codegen config with the same
// filename convention but roots that match its install layout.
const DOCUMENT_ROOTS = [
  "../../../addons/angee/*/web/src",
  "../addons/*/*/web/src",
  "./src",
] as const;

function documentGlobs(name: "public" | "console"): string[] {
  const files =
    name === "public"
      ? ["documents.public.ts"]
      : ["documents.ts", "documents.console.ts"];
  return DOCUMENT_ROOTS.flatMap((root) =>
    files.map((file) => `${root}/**/${file}`),
  );
}

export function schemaCodegen(name: "public" | "console"): CodegenConfig {
  return {
    schema: `../runtime/schemas/${name}.graphql`,
    documents: documentGlobs(name),
    // Pre-migration (no tagged operations yet) must still emit the factory.
    ignoreNoDocuments: true,
    generates: {
      [`../runtime/gql/${name}/`]: {
        preset: "client",
        presetConfig: { fragmentMasking: false },
        config: {
          scalars,
          enumsAsTypes: true,
          skipTypename: true,
          useTypeImports: true,
        },
      },
    },
  };
}
