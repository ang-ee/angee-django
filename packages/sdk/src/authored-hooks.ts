import { useCallback } from "react";
import type { TypedDocumentNode } from "@urql/core";

import { useDocumentMutation } from "./document-mutation";
import { useDocumentQuery } from "./document-query";
import {
  useDocumentSubscription,
  type DocumentSubscriptionOptions,
  type DocumentSubscriptionRun,
} from "./document-subscription";
import { useStableVariables } from "./stable-deps";
import type { DocumentData, DocumentVariables } from "./typed-document";

type AuthoredDocument = TypedDocumentNode<unknown, any>;
type AuthoredVariables<TDocument extends AuthoredDocument> =
  DocumentVariables<TDocument> extends Record<string, unknown>
    ? DocumentVariables<TDocument>
    : Record<string, never>;

export interface AuthoredQueryOptions {
  enabled?: boolean;
}

export interface AuthoredQueryResult<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Run a generated authored query document — the escape hatch for bespoke reads. */
export function useAuthoredQuery<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  options: AuthoredQueryOptions = {},
): AuthoredQueryResult<DocumentData<TDocument>> {
  const stable = useStableVariables(variables);
  const run = useDocumentQuery(document, stable, options.enabled ?? true);
  return {
    data: run.data as DocumentData<TDocument> | undefined,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

export type AuthoredMutate<TDocument extends AuthoredDocument> = (
  variables?: AuthoredVariables<TDocument>,
) => Promise<DocumentData<TDocument> | undefined>;

/** Run a generated authored mutation document; the runner throws on GraphQL error. */
export function useAuthoredMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
): [AuthoredMutate<TDocument>, { fetching: boolean; error: Error | null }] {
  type Data = DocumentData<TDocument>;
  type Variables = AuthoredVariables<TDocument>;
  const { execute, fetching, error } = useDocumentMutation<Data, Variables>(document);
  const mutate = useCallback<AuthoredMutate<TDocument>>(
    (variables) => execute((variables ?? {}) as Variables),
    [execute],
  );
  return [mutate, { fetching, error }];
}

export type AuthoredSubscriptionOptions<TData> =
  DocumentSubscriptionOptions<TData>;

/** Subscribe to a generated authored subscription document, firing `onData` per push. */
export function useAuthoredSubscription<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  options: AuthoredSubscriptionOptions<DocumentData<TDocument>> = {},
): DocumentSubscriptionRun<DocumentData<TDocument>> {
  return useDocumentSubscription(document, variables, options);
}
