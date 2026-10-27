const path = require("node:path");

// Install policy shared by standalone, development-stack and web-image roots.
const policy = {
  // These upstream assistant-ui composer regressions must be retested before lifting the pins.
  overrides: {
    "@assistant-ui/react": "0.14.26",
    "@assistant-ui/core": "0.2.20",
    "@assistant-ui/store": "0.2.19",
    "@assistant-ui/tap": "0.9.3",
  },
  // Refine exposes DocumentNode but omits graphql from its package metadata.
  packageExtensions: {
    "@refinedev/core@5.0.12": { dependencies: { graphql: "^16.14.0" } },
  },
  patchedDependencies: {
    "@refinedev/core@5.0.12": path.join(__dirname, "patches/refinedev-core-5.0.12.patch"),
  },
};

module.exports = {
  hooks: {
    updateConfig(config) {
      const packageExtensions = { ...config.packageExtensions };
      for (const [name, extension] of Object.entries(policy.packageExtensions)) {
        const current = packageExtensions[name];
        packageExtensions[name] = {
          ...current,
          ...extension,
          dependencies: { ...current?.dependencies, ...extension.dependencies },
        };
      }
      return {
        ...config,
        // Source workspaces need prepare outputs even when dependencies are unchanged.
        // pnpm's optimistic shortcut skips prepare and cannot detect missing dist files.
        optimisticRepeatInstall: false,
        overrides: { ...config.overrides, ...policy.overrides },
        packageExtensions,
        patchedDependencies: { ...config.patchedDependencies, ...policy.patchedDependencies },
      };
    },
  },
};
