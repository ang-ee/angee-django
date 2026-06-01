import { test as base, expect } from "@playwright/test";

import { GraphQLClient } from "./graphql";

export interface AngeeFixtures {
  /**
   * A GraphQL caller bound to this test's session (public schema). Use it to
   * assert backend state the UI does not surface, or to set up/tear down data a
   * spec mutates.
   */
  api: GraphQLClient;
}

/**
 * The base test for Angee e2e. Import `test` and `expect` from here — never from
 * `@playwright/test` directly — so the whole suite shares one Playwright
 * instance (a workspace package re-exporting fixtures otherwise risks the
 * "Requiring @playwright/test second time" dual-instance error).
 *
 * `api` is bound to the page's own request context (`page.request`), so it
 * carries the same session cookie as the browser — a spec that loads a role's
 * `storageState` queries GraphQL as that role, and an anonymous spec queries
 * anonymously.
 */
export const test = base.extend<AngeeFixtures>({
  api: async ({ page }, use) => {
    await use(new GraphQLClient(page.request));
  },
});

export { expect };
