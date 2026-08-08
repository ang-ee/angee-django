import { fileURLToPath } from "node:url";
import { defineAngeeWebVitestConfig, gqlAliasFor } from "../../../vitest.shared";

// This project owns its `runtime/gql/<schema>/` tree, so its tests resolve
// `@angee/gql/<schema>` against its OWN runtime/gql (project-relative) rather
// than the framework fixture default.
export default defineAngeeWebVitestConfig({
  gqlAlias: gqlAliasFor(
    fileURLToPath(new URL("../runtime/gql/", import.meta.url)),
  ),
});
