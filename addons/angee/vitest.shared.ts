import { fileURLToPath } from "node:url";

// The addon subtree's one fixture alias. Fragment configs import the neutral
// Vitest builder from @angee/app by package name, then supply this repo-specific
// generated-document target: the stack root's composed runtime/gql (a stack is
// the host; this repo is a slot at <stack>/workspaces/<ws>/<repo>).
export const gqlAlias = [
  {
    find: /^@angee\/gql\//,
    replacement: fileURLToPath(
      new URL("../../../../../runtime/gql/", import.meta.url),
    ),
  },
];
