// React-subtree package defaults. Reach the source that `@angee/app/vitest`
// exports by relative path because @angee/app's own config cannot resolve its
// package name through a self-symlink. The four framework packages are
// schema-independent and therefore need no composed-project fixture alias.
export { defineAngeePackageVitestConfig } from "./app/config/vitest";
