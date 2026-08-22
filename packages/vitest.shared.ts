// React-subtree package defaults. Reach the source that `@angee/app/vitest`
// exports by relative path because @angee/app's own config cannot resolve its
// package name through a self-symlink. Refine, metadata, and ui are
// schema-independent; app is the sanctioned composition package and supplies
// its composed stack runtime alias in app/vitest.config.ts.
export { defineAngeePackageVitestConfig } from "./app/config/vitest";
