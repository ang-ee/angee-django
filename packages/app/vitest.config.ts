import { fileURLToPath } from "node:url";

import { defineAngeePackageVitestConfig } from "../vitest.shared";
import { gqlAliasFor } from "./config/vitest";

const runtimeGql = fileURLToPath(
  new URL("../../../../runtime/gql/", import.meta.url),
);

export default defineAngeePackageVitestConfig({
  resolve: { alias: gqlAliasFor(runtimeGql) },
});
