import { fileURLToPath } from "node:url";
import {
  type AngeeWebVitestConfig,
  defineAngeePackageVitestConfig as definePackageVitestConfig,
  defineAngeeWebVitestConfig as defineWebVitestConfig,
  gqlAliasFor,
} from "./angee/web/app/config/vitest";

// Framework-repo fixture wrapper. The project-neutral Vitest builders are owned
// by `@angee/app/vitest` (shipped in the wheel, reached by package name for a
// downstream project); this module only adds the one fact that is specific to
// THIS repo: the in-repo base-addon web packages resolve `@angee/gql/<schema>`
// against the notes example's generated typed-document modules — the canonical
// in-repo project. It reaches those builders by RELATIVE path (the same source
// `@angee/app/vitest` exports) rather than the package specifier, because the
// in-repo consumers pull this module into their `tsc` typecheck and a package
// resolving its own name has no self-symlink. A downstream project imports
// `@angee/app/vitest` directly and passes its own gql alias; see
// `examples/notes-angee/web/vitest.config.ts` and the `templates/projects/web`
// scaffold.

export {
  type AngeeWebVitestConfig,
  gqlAliasFor,
} from "./angee/web/app/config/vitest";

// The in-repo fixture alias: base-addon `defineAngeeWebVitestConfig()` calls (no
// argument) resolve `@angee/gql/*` here. Absolute (resolved from this file) so
// every base-addon package, at any depth, resolves the same target.
export const gqlAlias = gqlAliasFor(
  fileURLToPath(new URL("./examples/notes-angee/runtime/gql/", import.meta.url)),
);

type PackageVitestConfig = NonNullable<Parameters<typeof definePackageVitestConfig>[0]>;
type PackageVitestResult = ReturnType<typeof definePackageVitestConfig>;
type AliasEntry = { find: string | RegExp; replacement: string };
type AliasList = AliasEntry[];

// Core framework packages resolve the same fixture: `@angee/ui` owns authored
// `documents.ts` view operations, so any core package whose test import graph
// reaches them needs the `@angee/gql/<schema>` alias too.
export function defineAngeePackageVitestConfig(
  config: PackageVitestConfig = {},
): PackageVitestResult {
  return definePackageVitestConfig(withGqlAlias(config));
}

// Defaults `gqlAlias` to the in-repo fixture so a base-addon config need not
// repeat it; a config that owns its own `runtime/gql/` passes `gqlAlias`.
export function defineAngeeWebVitestConfig(
  config: Partial<AngeeWebVitestConfig> = {},
): ReturnType<typeof defineWebVitestConfig> {
  return defineWebVitestConfig({ gqlAlias, ...config } as AngeeWebVitestConfig);
}

function withGqlAlias(config: PackageVitestConfig): PackageVitestConfig {
  return {
    ...config,
    resolve: {
      ...config.resolve,
      alias: [...gqlAlias, ...aliasArray(config.resolve?.alias)],
    },
  } as PackageVitestConfig;
}

function aliasArray(alias: unknown): AliasList {
  if (!alias) return [];
  if (Array.isArray(alias)) return alias as AliasList;
  return Object.entries(alias as Record<string, string>).map(([find, replacement]) => ({
    find,
    replacement,
  }));
}
