import { useCallback } from "react";
import { useSubscription as useUrqlSubscription } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useDocumentMutation } from "./document-mutation";
import { useDocumentQuery } from "./document-query";
import { useStableVariables } from "./stable-deps";

type Variables = Record<string, unknown>;

export interface AuthoredQueryOptions {
  enabled?: boolean;
}

export interface AuthoredQueryResult<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Run a hand-authored query document — the escape hatch for bespoke reads. */
export function useAuthoredQuery<TData = Variables, TVariables extends Variables = Variables>(
  document: string,
  variables?: TVariables,
  options: AuthoredQueryOptions = {},
): AuthoredQueryResult<TData> {
  const stable = useStableVariables(variables);
  const run = useDocumentQuery(document, stable, options.enabled ?? true);
  return {
    data: run.data as TData | undefined,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

export type AuthoredMutate<TData, TVariables> = (
  variables?: TVariables,
) => Promise<TData | undefined>;

/** Run a hand-authored mutation document; the runner throws on GraphQL error. */
export function useAuthoredMutation<TData = Variables, TVariables extends Variables = Variables>(
  document: string,
): [AuthoredMutate<TData, TVariables>, { fetching: boolean; error: Error | null }] {
  const { execute, fetching, error } = useDocumentMutation<TData, TVariables>(document);
  const mutate = useCallback<AuthoredMutate<TData, TVariables>>(
    (variables) => execute((variables ?? {}) as TVariables),
    [execute],
  );
  return [mutate, { fetching, error }];
}

export interface AuthoredSubscriptionOptions<TData> {
  enabled?: boolean;
  onData?: (data: TData) => void;
}

/** Subscribe to a hand-authored subscription document, firing `onData` per push. */
export function useAuthoredSubscription<TData = Variables, TVariables extends Variables = Variables>(
  document: string,
  variables?: TVariables,
  options: AuthoredSubscriptionOptions<TData> = {},
): { data: TData | undefined; fetching: boolean; error: Error | null } {
  const enabled = options.enabled ?? true;
  const stable = useStableVariables(variables);
  const { onData } = options;
  const [state] = useUrqlSubscription<TData, TData, TVariables>(
    {
      query: enabled ? document : DISABLED_DOCUMENTS.subscription,
      variables: stable,
      pause: !enabled,
    },
    (_previous, value) => {
      onData?.(value);
      return value;
    },
  );
  return {
    data: state.data,
    fetching: state.fetching,
    error: state.error ?? null,
  };
}
