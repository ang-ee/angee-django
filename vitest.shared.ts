import { fileURLToPath } from "node:url";

// The `@angee/gql/<schema>` alias for test runs. Vitest does not read tsconfig
// `paths`, so addon test suites that load a module importing `@angee/gql/<schema>`
// need this alias explicitly.
//
// Framework-repo fixture wiring: addon package tests run against the notes
// example project's generated typed-document modules. Absolute (resolved from
// this file) so every package, at any depth, resolves the same target.
export const gqlAlias = [
  {
    find: /^@angee\/gql\//,
    replacement: fileURLToPath(
      new URL("./examples/notes-angee/runtime/gql/", import.meta.url),
    ),
  },
];
