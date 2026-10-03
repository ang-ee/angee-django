import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { searchForWorkspaceRoot } from "vite";
import { defineConfig, mergeConfig, type ViteUserConfig } from "vitest/config";
import type { InlineConfig } from "vitest/node";

// Never watch Jujutsu's `.jj/` store: a watcher there slows Vitest startup, times
// out `jj` commands, and corrupts `working_copy.lock` (see the jj FAQ). Vite
// already ignores `.git/` and `node_modules/`. Kept inline here and in `./vite.ts`:
// these config modules load through Node's own ESM loader in consumers, which
// does not resolve extensionless relative imports.
const ANGEE_WATCH_IGNORED: readonly string[] = ["**/.jj/**"];

// The framework owner of the web/package Vitest defaults: the DOM-inline set, the
// `src/**` test globs, and the generated-schema alias builder. Shipped in `@angee/app` (not a repo-root file) so a project
// reaches it by package name whether the framework is an editable checkout or an
// installed package. These builders carry no framework-repo fixture. Framework
// packages use schema-independent defaults; addon/project configs supply their
// own generated-document alias when they consume a composed schema.

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

const testDefaults = defineConfig({
  server: {
    // Watch mode reuses Vite's dev-server watcher; keep it off jj's store.
    watch: { ignored: [...ANGEE_WATCH_IGNORED] },
    // DOM suites load the shared setup file through Vite's file server. A
    // consumer outside this repository (an external addon slot) has its own
    // workspace root, so allow this config directory beside Vite's default.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), fileURLToPath(new URL(".", import.meta.url))] },
  },
  test: {
    // Pure modules run under node; hook/component suites opt into a DOM
    // environment per-file with a `// @vitest-environment happy-dom` pragma.
    environment: "node",
    setupFiles: [fileURLToPath(new URL("./vitest-setup.js", import.meta.url))],
    include: srcTestIncludes,
    server: {
      // The chrome barrel pulls in the logo stylesheet; inline it so Vite
      // resolves the CSS import instead of Node's ESM loader rejecting it
      // in both package and addon tests.
      deps: { inline: ["@angee/logo-react"] },
    },
  },
});

export function defineAngeePackageVitestConfig(
  config: ViteUserConfig = {},
): ViteUserConfig {
  return mergeConfig(testDefaults, config);
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
    mergeConfig(testDefaults, { resolve: { alias: gqlAlias } }),
    {
      ...config,
      test: include === undefined ? testConfig : { ...testConfig, include },
    },
  );
}
