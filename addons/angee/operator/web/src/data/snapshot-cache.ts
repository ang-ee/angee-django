import { authoredQueryKey } from "@angee/refine";
import type { QueryClient } from "@tanstack/react-query";

import { SNAPSHOT_QUERY } from "./documents.daemon";
import { OPERATOR_PROVIDER } from "./operator-provider";
import type { OperatorSnapshotQueryData, OperatorSnapshotQueryVariables } from "./types";

const sectionFields = {
  wantOverview: ["health", "stackStatus"],
  wantServices: ["services"],
  wantWorkspaces: ["workspaces"],
  wantSources: ["sources"],
  wantGitOps: ["gitOpsTopology"],
  wantOperations: ["jobs"],
  wantTemplates: ["templates"],
  wantSecrets: ["secrets"],
} as const satisfies Record<keyof OperatorSnapshotQueryVariables, readonly (keyof OperatorSnapshotQueryData)[]>;

/** Project each push into the fields requested by each native Query entry.
 * Missing fields in a partial update preserve cached fields; explicit null/empty
 * fields replace them. A different pane's HTTP request cannot erase this entry.
 */
export function updateSnapshotQueries(client: QueryClient, pushed: OperatorSnapshotQueryData): void {
  const prefix = authoredQueryKey(SNAPSHOT_QUERY, undefined, OPERATOR_PROVIDER).slice(0, 5);
  for (const query of client.getQueryCache().findAll({ queryKey: prefix })) {
    const variables = query.queryKey[5] as OperatorSnapshotQueryVariables;
    client.setQueryData<OperatorSnapshotQueryData>(query.queryKey, (previous) => {
      const patch: Record<string, unknown> = {};
      for (const [flag, fields] of Object.entries(sectionFields)) {
        if (!variables[flag as keyof OperatorSnapshotQueryVariables]) continue;
        for (const field of fields) {
          if (Object.hasOwn(pushed, field)) patch[field] = pushed[field];
        }
      }
      return Object.keys(patch).length ? { ...previous, ...patch } : previous;
    });
  }
}
