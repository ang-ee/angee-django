import { existsSync } from "node:fs";
import { defineConfig, mergeConfig, type ViteUserConfig } from "vitest/config";
import type { InlineConfig } from "vitest/node";

// The framework owner of the web/package Vitest defaults: the DOM-inline set, the
// `src/**` test globs, and the generated-schema alias builder. Shipped in `@angee/app` (not a repo-root file) so a project
// reaches it by package name whether the framework is an editable checkout or an
// installed wheel. These builders carry NO framework-repo fixture: the gql alias
// is always supplied by the caller (the repo-root `vitest.shared.ts` wrapper
// injects the in-repo notes fixture; a project passes its own).

// The generated-schema module alias for test runs. Vitest does not read
// tsconfig `paths`, so a suite that loads a generated document import needs
// this alias supplied explicitly via Vite `resolve.alias`.
//
// `gqlAliasFor` is the project-neutral builder: pass the absolute path to a
// project's `runtime/gql/` tree (the directory it generated) and it returns the
// single-wildcard alias that maps schema and schema-action modules into it. A
// project's own `vitest.config.ts` calls this with its project-relative path — e.g.
// `gqlAliasFor(fileURLToPath(new URL("../runtime/gql/", import.meta.url)))`.
export function gqlAliasFor(runtimeGqlDir: string) {
  if (!existsSync(runtimeGqlDir)) {
    throw new Error(
      `Generated GraphQL runtime not found at "${runtimeGqlDir}". `
      + "Compose the stack first, then run pnpm codegen from the stack web host.",
    );
  }
  return [
    {
      find: /^@angee\/gql\//,
      replacement: runtimeGqlDir,
    },
  ];
}

const srcTestIncludes = ["src/**/*.test.ts", "src/**/*.test.tsx"];

const packageDefaults = defineConfig({
  test: {
    // Pure modules run under node; hook/component suites opt into a DOM
    // environment per-file with a `// @vitest-environment happy-dom` pragma.
    environment: "node",
    include: srcTestIncludes,
    server: {
      // The chrome barrel pulls in the logo stylesheet; inline it so Vite
      // resolves the CSS import instead of Node's ESM loader rejecting it
      // (same rationale as the web defaults below).
      deps: { inline: ["@angee/logo-react"] },
    },
  },
});

const webDefaults = defineConfig({
  test: {
    environment: "node",
    include: srcTestIncludes,
    server: {
      // The chrome barrel pulls in the logo stylesheet; inline it so Vite
      // resolves the CSS import instead of Node's ESM loader rejecting it.
      deps: { inline: ["@angee/logo-react"] },
    },
  },
});

export function defineAngeePackageVitestConfig(
  config: ViteUserConfig = {},
): ViteUserConfig {
  return mergeConfig(packageDefaults, config);
}

export interface AngeeWebVitestConfig extends ViteUserConfig {
  /**
   * The generated-schema alias this package's tests resolve against, built with
   * `gqlAliasFor`. Required — these builders carry no framework fixture, so
   * the caller always names the `runtime/gql/` its tests resolve into.
   */
  gqlAlias: ReturnType<typeof gqlAliasFor>;
  test?: InlineConfig & {
    /** Package-specific test globs appended after the shared `src/**` defaults. */
    extraInclude?: string[];
  };
}

export function defineAngeeWebVitestConfig({
  gqlAlias,
  test,
  ...config
}: AngeeWebVitestConfig): ViteUserConfig {
  const { extraInclude = [], ...testConfig } = test ?? {};
  const include = extraInclude.length ? extraInclude : testConfig.include;
  return mergeConfig(
    mergeConfig(webDefaults, { resolve: { alias: gqlAlias } }),
    {
      ...config,
      test: include === undefined ? testConfig : { ...testConfig, include },
    },
  );
}
