import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";

import { resolveBaseURL } from "./env";

export interface E2EConfigOptions {
  /** Directory holding specs and the auth setup, relative to the config. */
  testDir?: string;
  /** Extra Playwright config; `use` deep-merges over the framework defaults so
   * `baseURL` survives, `projects` is replaced only when explicitly provided. */
  overrides?: PlaywrightTestConfig;
}

/**
 * The framework-owned Playwright config. A consumer's `playwright.config.ts` is
 * one line: `export default defineE2EConfig()`.
 *
 * `baseURL` is read from the angee workspace environment, so the same config
 * drives every workspace unchanged. Two projects: `setup` authenticates each
 * role and persists its `storageState` (see `auth.setup.ts`); `chromium` runs
 * the specs and depends on `setup`. Specs opt into a role with
 * `test.use({ storageState: roleStatePath("alice") })`; specs that set none run
 * anonymously (e.g. the login-UI and permission-denied flows).
 *
 * Playwright's default `testMatch` collects `*.spec.ts` / `*.test.ts`, so the
 * `*.setup.ts` file runs only under the `setup` project — name files to match.
 */
export function defineE2EConfig(
  options: E2EConfigOptions = {},
): PlaywrightTestConfig {
  const isCI = Boolean(process.env.CI);
  // Pull framework-owned seams out of the flat spread so a consumer override
  // can't silently drop baseURL or the setup dependency (e2e-003).
  const { use: useOverride, projects: projectsOverride, ...restOverrides } = options.overrides ?? {};
  return defineConfig({
    testDir: options.testDir ?? "tests",
    fullyParallel: true,
    forbidOnly: isCI,
    retries: isCI ? 1 : 0,
    reporter: [["list"], ["html", { open: "never" }]],
    use: {
      baseURL: resolveBaseURL(),
      trace: "on-first-retry",
      screenshot: "only-on-failure",
      ...useOverride,
    },
    projects: projectsOverride ?? [
      { name: "setup", testMatch: /.*\.setup\.ts/ },
      {
        name: "chromium",
        use: { ...devices["Desktop Chrome"] },
        dependencies: ["setup"],
      },
    ],
    ...restOverrides,
  });
}
