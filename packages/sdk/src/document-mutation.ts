import { useCallback } from "react";
import type { AnyVariables, DocumentInput } from "@urql/core";
import { useMutation as useUrqlMutation } from "urql";

import { useBusyRun } from "./use-busy-run";

export interface DocumentMutationRun<TData, TVariables> {
  /** Run the mutation; resolves to `result.data`, throwing on a GraphQL error. */
  execute: (variables: TVariables) => Promise<TData | undefined>;
  fetching: boolean;
  error: Error | null;
}

/**
 * The shared write seam: run one mutation document, throw on GraphQL error, and
 * expose a stable `execute` plus a uniform `{ fetching, error }`. Every mutation
 * hook (resource create/update/delete, authored mutations, auth login/logout)
 * routes through this so the run / error-throw / status-shape logic lives in one
 * place — the write counterpart of `useDocumentQuery`. Callers layer their own
 * post-success side effects (cache invalidation, client reset) and data shaping
 * on top of `execute`.
 *
 * `document` is urql's `DocumentInput`: a runtime-built query string (resource
 * CRUD, auth) or a generated `TypedDocumentNode` (authored operations) that
 * carries its own data/variables types — urql infers them either way.
 */
export function useDocumentMutation<
  TData = unknown,
  TVariables extends AnyVariables = Record<string, unknown>,
>(document: DocumentInput<TData, TVariables>): DocumentMutationRun<TData, TVariables> {
  const [state, run] = useUrqlMutation<TData, TVariables>(document);
  const busy = useBusyRun();
  const execute = useCallback(
    (variables: TVariables): Promise<TData | undefined> => busy.run(async () => {
      const result = await run(variables);
      if (result.error) throw result.error;
      return result.data ?? undefined;
    }),
    [busy.run, run],
  );
  return { execute, fetching: busy.busy, error: state.error ?? null };
}
