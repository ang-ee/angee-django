import { fileURLToPath } from "node:url";
import { defineAngeeWebViteConfig } from "@angee/app/vite";

// The framework owns the plugin pair, the dev-server host/port/proxy wiring, and
// the project-derived optimizeDeps set (see `@angee/app/vite`). This config
// supplies only the two project facts: the in-repo example consumes the
// `@angee/*` packages as linked workspace source, so it does NOT pre-bundle
// them; and its `@angee/gql/<schema>` alias points at its OWN `runtime/gql/`.
export default defineAngeeWebViteConfig({
  prebundleAngeePackages: false,
  webRoot: fileURLToPath(new URL(".", import.meta.url)),
  gqlRuntimeDir: fileURLToPath(new URL("../runtime/gql/", import.meta.url)),
});
