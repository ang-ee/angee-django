import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The addon subtree's one fixture alias. Fragment configs import the neutral
// Vitest builder from @angee/app by package name, then supply this repo-specific
// generated-document target. Match tsconfig.base.json: the containing stack owns
// composed output; a standalone checkout may fall back to .angee/runtime.
const localGql = fileURLToPath(
  new URL("../../.angee/runtime/gql/", import.meta.url),
);
const stackGql = fileURLToPath(
  new URL("../../../../../runtime/gql/", import.meta.url),
);

export const gqlAlias = [
  {
    find: /^@angee\/gql\//,
    replacement: existsSync(stackGql) ? stackGql : localGql,
  },
];
