import { fileURLToPath } from "node:url";

// The addon subtree's one fixture alias. Fragment configs import the neutral
// Vitest builder from @angee/app by package name, then supply this repo-specific
// generated-document target.
export const gqlAlias = [
  {
    find: /^@angee\/gql\//,
    replacement: fileURLToPath(
      new URL("../../examples/notes-angee/runtime/gql/", import.meta.url),
    ),
  },
];
