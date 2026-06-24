import { fileURLToPath } from "node:url";
import { defineConfig, mergeConfig, type ViteUserConfig } from "vitest/config";
import type { InlineConfig } from "vitest/node";

// The `@angee/gql/<schema>` alias for test runs. Vitest does not read tsconfig
// `paths`, so addon test suites that load a module importing `@angee/gql/<schema>`
// need this alias explicitly.
//
// Framework-repo fixture wiring: addon package tests run against the notes
// example project's generated typed-document modules. Absolute (resolved from
// this file) so every package, at any depth, resolves the same target.
export const gqlAlias = [
  {
    find: /^@angee\/gql\//,
    replacement: fileURLToPath(
      new URL("./examples/notes-angee/runtime/gql/", import.meta.url),
    ),
  },
];

const srcTestIncludes = ["src/**/*.test.ts", "src/**/*.test.tsx"];

const packageDefaults = defineConfig({
  test: {
    // Pure modules run under node; hook/component suites opt into a DOM
    // environment per-file with a `// @vitest-environment happy-dom` pragma.
    environment: "node",
    include: srcTestIncludes,
  },
});

const webDefaults = defineConfig({
  resolve: { alias: gqlAlias },
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
  test?: InlineConfig & {
    /** Package-specific test globs appended after the shared `src/**` defaults. */
    extraInclude?: string[];
  };
}

export function defineAngeeWebVitestConfig({
  test,
  ...config
}: AngeeWebVitestConfig = {}): ViteUserConfig {
  const { extraInclude = [], ...testConfig } = test ?? {};
  const include = extraInclude.length ? extraInclude : testConfig.include;
  return mergeConfig(
    webDefaults,
    {
      ...config,
      test: include === undefined ? testConfig : { ...testConfig, include },
    },
  );
}
