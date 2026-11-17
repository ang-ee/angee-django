/// <reference path="./host-module.d.ts" />

// The running stack's emitted composition, resolved by Storybook's Vite config.
// Product workshops replace schema transports with fixtures before rendering it.
export { composedAddons, loadComposedSchemas } from "virtual:angee-storybook-host";
