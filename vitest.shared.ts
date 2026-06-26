import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, mergeConfig, type ViteUserConfig } from "vitest/config";
import type { InlineConfig } from "vitest/node";

const require = createRequire(
  fileURLToPath(new URL("./packages/base/package.json", import.meta.url)),
);
const refineReactTableRoot = dirname(
  dirname(require.resolve("@refinedev/react-table")),
);
const lodashIsEqual = join(
  dirname(dirname(refineReactTableRoot)),
  "lodash",
  "isEqual.js",
);

// The `@angee/gql/<schema>` alias for test runs. Vitest does not read tsconfig
// `paths`, so a test suite that loads a module importing `@angee/gql/<schema>`
// needs this alias supplied explicitly via Vite `resolve.alias`.
//
// `gqlAliasFor` is the project-neutral builder: pass the absolute path to a
// project's `runtime/gql/` tree (the directory it generated) and it returns the
// single-wildcard alias that maps `@angee/gql/<schema>` (and
// `@angee/gql/<schema>/actions`) into it. A downstream project's own
// `vitest.config.ts` calls this with its project-relative path — e.g.
// `gqlAliasFor(fileURLToPath(new URL("../runtime/gql/", import.meta.url)))`.
export function gqlAliasFor(runtimeGqlDir: string) {
  return [
    {
      find: /^@angee\/gql\//,
      replacement: runtimeGqlDir,
    },
  ];
}

// Framework-repo fixture wiring ONLY: in-repo base-addon package tests
// (`addons/angee/*/web`) run against the notes example project's generated
// typed-document modules — the canonical in-repo project. This is the default
// for `defineAngeeWebVitestConfig()` so base addons resolve `@angee/gql/*`
// without each config repeating the path; a project that owns its own
// `runtime/gql/` passes its own alias instead (see
// `examples/notes-angee/web/vitest.config.ts`). Absolute (resolved from this
// file) so every base-addon package, at any depth, resolves the same target.
export const gqlAlias = gqlAliasFor(
  fileURLToPath(new URL("./examples/notes-angee/runtime/gql/", import.meta.url)),
);

const refineTestAlias = [
  {
    find: "lodash/isEqual",
    replacement: lodashIsEqual,
  },
];

const srcTestIncludes = ["src/**/*.test.ts", "src/**/*.test.tsx"];

const packageDefaults = defineConfig({
  resolve: { alias: refineTestAlias },
  test: {
    // Pure modules run under node; hook/component suites opt into a DOM
    // environment per-file with a `// @vitest-environment happy-dom` pragma.
    environment: "node",
    include: srcTestIncludes,
    server: {
      deps: { inline: ["@refinedev/react-table"] },
    },
  },
});

// Web defaults carry NO gql alias — `defineAngeeWebVitestConfig` injects the
// project's gql alias (the framework fixture by default, a project-relative one
// when supplied) so the helper stays project-neutral and a downstream project
// can point tests at its own `runtime/gql/`.
const webDefaults = defineConfig({
  resolve: { alias: refineTestAlias },
  test: {
    environment: "node",
    include: srcTestIncludes,
    server: {
      // The chrome barrel pulls in the logo stylesheet; inline it so Vite
      // resolves the CSS import instead of Node's ESM loader rejecting it.
      deps: { inline: ["@angee/logo-react", "@refinedev/react-table"] },
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
   * The `@angee/gql/<schema>` alias this package's tests resolve against,
   * built with `gqlAliasFor`. Defaults to the framework fixture (the notes
   * example's `runtime/gql/`) so in-repo base-addon configs need not repeat it.
   * A project that owns its own `runtime/gql/` passes its project-relative
   * alias here.
   */
  gqlAlias?: ReturnType<typeof gqlAliasFor>;
  test?: InlineConfig & {
    /** Package-specific test globs appended after the shared `src/**` defaults. */
    extraInclude?: string[];
  };
}

export function defineAngeeWebVitestConfig({
  gqlAlias: projectGqlAlias = gqlAlias,
  test,
  ...config
}: AngeeWebVitestConfig = {}): ViteUserConfig {
  const { extraInclude = [], ...testConfig } = test ?? {};
  const include = extraInclude.length ? extraInclude : testConfig.include;
  return mergeConfig(
    mergeConfig(webDefaults, { resolve: { alias: projectGqlAlias } }),
    {
      ...config,
      test: include === undefined ? testConfig : { ...testConfig, include },
    },
  );
}
