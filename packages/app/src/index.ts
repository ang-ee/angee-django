// @angee/app — the composition layer. The single composition root
// (`createApp` — the one `<Refine>`/QueryClient/liveProvider/createRoot owner)
// and the addon-composition API (`defineAddon`/`composeAddons`/`defineBaseAddon`)
// that folds addon manifests into one runtime. The only package that depends on
// every layer below (refine · metadata · ui).

// App composition root + the rendered-addon seam.
export * from "./create-app";

// Addon composition API (headless manifest authoring + folding).
export * from "./define-addon";

// The login/OAuth-callback auth surface — app-shell pages the host mounts as
// routes (the only consumers are addon web + the host, never a package below).
export * from "./auth";

export * from "./providers/auth";
export * from "./providers/i18n";
