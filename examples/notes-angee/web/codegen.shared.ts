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

// Roots that author operations for the notes example project: framework frontend
// packages, framework addon web packages in this monorepo, this project's
// consumer addons, and the app layout itself. These monorepo-layout globs are
// the DEFAULT — they match the in-repo example's workspace layout.
//
// A downstream project installs its addons under `node_modules` instead, so it
// overrides the roots without forking this file: set the
// `ANGEE_CODEGEN_DOCUMENT_ROOTS` env var (a list separated by the OS path
// delimiter `:` / `;`, or by commas) to its own layout, e.g.
// `node_modules/@angee/*/web/src:./addons/*/*/web/src:./src`. A project that
// prefers a checked-in config can instead pass roots through its own
// `codegen.{public,console}.ts` (see `schemaCodegen`'s `documentRoots` option).
// The roots are sorted so an env-supplied list emits a deterministic glob set.
const DEFAULT_DOCUMENT_ROOTS = [
  "../../../addons/angee/*/web/src",
  "../../../packages/*/src",
  "../addons/*/*/web/src",
  "./src",
];

function documentRootsFromEnv(): string[] | undefined {
  const raw = process.env.ANGEE_CODEGEN_DOCUMENT_ROOTS?.trim();
  if (!raw) return undefined;
  return raw
    .split(/[,;:]/)
    .map((root) => root.trim())
    .filter((root) => root.length > 0);
}

function documentGlobs(name: "public" | "console", roots: string[]): string[] {
  const files =
    name === "public"
      ? ["documents.public.ts"]
      : ["documents.ts", "documents.console.ts"];
  return [...roots]
    .sort()
    .flatMap((root) => files.map((file) => `${root}/**/${file}`));
}

export function schemaCodegen(
  name: "public" | "console",
  // Effective document roots, highest precedence first: an explicit override
  // (a downstream project's own codegen.{public,console}.ts), then the
  // `ANGEE_CODEGEN_DOCUMENT_ROOTS` env var, then the monorepo defaults.
  documentRoots: string[] = documentRootsFromEnv() ?? DEFAULT_DOCUMENT_ROOTS,
): CodegenConfig {
  return {
    schema: `../runtime/schemas/${name}.graphql`,
    documents: documentGlobs(name, documentRoots),
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
