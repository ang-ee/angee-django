import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The addon subtree's one fixture alias. Fragment configs import the neutral
// Vitest builder from @angee/app by package name, then supply this repo-specific
// generated-document target. A source checkout may cache the composed output
// under .angee/runtime; otherwise the containing stack remains the host.
const localGql = fileURLToPath(
  new URL("../../.angee/runtime/gql/", import.meta.url),
);
const stackGql = fileURLToPath(
  new URL("../../../../../runtime/gql/", import.meta.url),
);

export const gqlAlias = [
  {
    find: /^@angee\/gql\//,
    replacement: existsSync(localGql) ? localGql : stackGql,
  },
];
