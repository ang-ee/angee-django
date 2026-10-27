const path = require("node:path");

// Install policy shared by standalone, development-stack and web-image roots.
const policy = {
  // These upstream assistant-ui composer regressions must be retested before lifting the pins.
  overrides: {
    // Retest the native memory-history snapshot patch before lifting this pin.
    "nuqs": "2.10.1",
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
    // TanStack memory histories must own the full Nuqs write snapshot, not window.location.
    "nuqs@2.10.1": path.join(__dirname, "patches/nuqs-2.10.1.patch"),
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
        overrides: { ...config.overrides, ...policy.overrides },
        packageExtensions,
        patchedDependencies: { ...config.patchedDependencies, ...policy.patchedDependencies },
      };
    },
  },
};
