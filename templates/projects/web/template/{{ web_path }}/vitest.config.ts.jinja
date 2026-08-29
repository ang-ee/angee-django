import { fileURLToPath } from "node:url";
import { defineAngeeWebVitestConfig, gqlAliasFor } from "@angee/app/vitest";

// This project owns its `runtime/gql/<schema>/` tree, so its tests resolve
// `@angee/gql/<schema>` against its OWN runtime/gql (project-relative). Vitest
// does not read tsconfig `paths`, so the alias is supplied explicitly here — the
// same shape this project declares in its tsconfig/vite. The shared web defaults
// (refine aliases, DOM deps, test globs) come from `@angee/app/vitest`, reached
// by package name so it resolves against an editable checkout or an installed
// wheel alike — no sibling path.
export default defineAngeeWebVitestConfig({
  gqlAlias: gqlAliasFor(
    fileURLToPath(new URL("../runtime/gql/", import.meta.url)),
  ),
  test: {
    extraInclude: ["*.test.ts"],
  },
});
