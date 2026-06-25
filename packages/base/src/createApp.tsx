// Re-export shim. The app composition root (the single `<Refine>`/QueryClient/
// liveProvider/createRoot owner) now lives in `@angee/app`; this preserves the
// `@angee/base` import surface for the host + addons. It MUST NOT instantiate
// anything — it only forwards the moved symbols.
export * from "@angee/app/create-app";
