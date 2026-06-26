import { fileURLToPath } from "node:url";
import { defineAngeeWebVitestConfig, gqlAliasFor } from "../../../vitest.shared";

// This project owns its `runtime/gql/<schema>/` tree, so its tests resolve
// `@angee/gql/<schema>` against its OWN runtime/gql (project-relative) rather
// than the framework fixture default. A downstream project's web vitest config
// does the same — this is the canonical example of that override.
export default defineAngeeWebVitestConfig({
  gqlAlias: gqlAliasFor(
    fileURLToPath(new URL("../runtime/gql/", import.meta.url)),
  ),
  test: {
    extraInclude: ["*.test.ts"],
  },
});
