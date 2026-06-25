// Re-export shim. The createApp test harness moved to `@angee/app` alongside
// the createApp it mounts; this preserves the `@angee/base/testing` import
// surface (storybook preview, addon chrome pins). It instantiates nothing.
export * from "@angee/app/testing";
