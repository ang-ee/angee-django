// @ts-check

import { existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gqlStubPlugin } from "./gql-stub.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
// Storybook runs from the stack root (its runtime/gql is the composed schema),
// so discover every addon's web stories across the stack from there instead of
// from this package's position. This covers all available addons — the stack
// root's own `addons/` and each workspace source slot's — without naming any
// specific slot repository; a standalone checkout just finds its own `addons/`.
const STACK_ROOT = process.env.INIT_CWD ?? process.cwd();
const SLOT_PARENT = join(STACK_ROOT, "workspaces", "src");
const addonRoots = [
  join(STACK_ROOT, "addons"),
  ...(existsSync(SLOT_PARENT)
    ? readdirSync(SLOT_PARENT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => join(SLOT_PARENT, entry.name, "addons"))
    : []),
].filter((directory) => existsSync(directory));
const addonStories = addonRoots.map((directory) => ({
  directory,
  files: "*/*/web/src/**/*.stories.@(ts|tsx)",
}));

/** @type {import("@storybook/react-vite").StorybookConfig} */
const config = {
  framework: "@storybook/react-vite",
  stories: [
    "../src/stories/**/*.stories.@(ts|tsx)",
    ...addonStories,
  ],
  addons: [],
  typescript: { reactDocgen: false },
  viteFinal: async (vite) => {
    const tailwind = (await import("@tailwindcss/vite")).default;
    // When runtime/gql is absent (standalone checkout), inject a stub plugin
    // so addons importing @angee/gql/* don't cause a pre-bundling hard error.
    const gqlPlugins = existsSync(join(STACK_ROOT, "runtime/gql"))
      ? []
      : [gqlStubPlugin()];
    vite.plugins = [...(vite.plugins ?? []), tailwind(), ...gqlPlugins];
    // Suppress the HMR error overlay for missing addons that require the full
    // stack runtime (e.g. @angee/gql/console from proposals addon). These are
    // expected missing deps in a standalone checkout — the overlay blocks the UI.
    vite.server = { ...vite.server, hmr: { ...vite.server?.hmr, overlay: false } };
    vite.resolve = {
      ...(vite.resolve ?? {}),
      preserveSymlinks: true,
      dedupe: ["react", "react-dom"],
      alias: {
        ...(vite.resolve?.alias ?? {}),
        // Only alias @angee/gql when the full stack runtime/gql is present.
        // In a standalone checkout this directory does not exist and addon
        // stories that import from it (e.g. proposals) are simply skipped.
        ...(existsSync(join(STACK_ROOT, "runtime/gql"))
          ? { "@angee/gql": join(STACK_ROOT, "runtime/gql") }
          : {}),
        react: join(ROOT, "node_modules/react"),
        "react-dom": join(ROOT, "node_modules/react-dom"),
        "react-dom/client": join(ROOT, "node_modules/react-dom/client"),
        "react/jsx-runtime": join(ROOT, "node_modules/react/jsx-runtime"),
      },
    };
    return vite;
  },
};

export default config;
