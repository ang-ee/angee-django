import { fileURLToPath } from "node:url";
import type { ViteUserConfig } from "vitest/config";
import {
  type AngeeWebVitestConfig,
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
  defineAngeePackageVitestConfig,
  gqlAliasFor,
} from "./angee/web/app/config/vitest";

// The in-repo fixture alias: base-addon `defineAngeeWebVitestConfig()` calls (no
// argument) resolve `@angee/gql/*` here. Absolute (resolved from this file) so
// every base-addon package, at any depth, resolves the same target.
export const gqlAlias = gqlAliasFor(
  fileURLToPath(new URL("./examples/notes-angee/runtime/gql/", import.meta.url)),
);

// Defaults `gqlAlias` to the in-repo fixture so a base-addon config need not
// repeat it; a config that owns its own `runtime/gql/` passes `gqlAlias`.
export function defineAngeeWebVitestConfig(
  config: Partial<AngeeWebVitestConfig> = {},
): ViteUserConfig {
  return defineWebVitestConfig({ gqlAlias, ...config });
}
