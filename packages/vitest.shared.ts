// React-subtree package defaults. Reach the source that `@angee/app/vitest`
// exports by relative path because @angee/app's own config cannot resolve its
// package name through a self-symlink. All framework packages, including app,
// are schema-independent. Addon/project configs supply their own generated
// document aliases at the composition boundary.
export { defineAngeePackageVitestConfig } from "./app/config/vitest";
