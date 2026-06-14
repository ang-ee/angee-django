import { useCallback } from "react";
import { useMutation as useUrqlMutation } from "urql";

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
 */
export function useDocumentMutation<
  TData = unknown,
  TVariables extends object = Record<string, unknown>,
>(document: string): DocumentMutationRun<TData, TVariables> {
  const [state, run] = useUrqlMutation<TData, TVariables>(document);
  const execute = useCallback(
    async (variables: TVariables): Promise<TData | undefined> => {
      const result = await run(variables);
      if (result.error) throw result.error;
      return result.data ?? undefined;
    },
    [run],
  );
  return { execute, fetching: state.fetching, error: state.error ?? null };
}
