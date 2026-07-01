export { test, expect, type AngeeFixtures } from "./fixtures";
export { defineE2EConfig, type E2EConfigOptions } from "./config";
export {
  GraphQLClient,
  type GraphQLResult,
  type GraphQLError,
  PUBLIC_GRAPHQL_PATH,
  CSRF_PATH,
} from "./graphql";
export { loginViaApi, roleStatePath, type Credentials } from "./auth";
export { PageObject } from "./pom";
export { resolveBaseURL } from "./env";
// Type-only re-export so Page Objects never import from @playwright/test (the
// dual-instance trap, docs/testing/e2e.md); types are erased, so this is safe.
export type { Locator, Page } from "@playwright/test";
