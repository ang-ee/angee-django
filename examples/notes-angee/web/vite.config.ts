import { fileURLToPath } from "node:url";
import { defineAngeeWebViteConfig } from "../../../vite.shared";

// The framework owns the plugin pair, the dev-server host/port/proxy wiring, and
// the project-derived optimizeDeps set (see `vite.shared.ts`). This config
// supplies only the two project facts: the in-repo example consumes the
// `@angee/*` packages as linked workspace source, so it does NOT pre-bundle
// them; and its `@angee/gql/<schema>` alias points at its OWN `runtime/gql/`.
export default defineAngeeWebViteConfig({
  prebundleAngeePackages: false,
  gqlRuntimeDir: fileURLToPath(new URL("../runtime/gql/", import.meta.url)),
});
