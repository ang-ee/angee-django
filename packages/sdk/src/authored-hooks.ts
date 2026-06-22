import { useCallback, useMemo } from "react";
import type { TypedDocumentNode } from "@urql/core";

import { useDocumentMutation } from "./document-mutation";
import { useDocumentQuery } from "./document-query";
import {
  useInvalidateModels,
  useRegisterModelsRefetch,
} from "./relay-invalidation";
import {
  useDocumentSubscription,
  type DocumentSubscriptionOptions,
  type DocumentSubscriptionRun,
} from "./document-subscription";
import { useStableArray, useStableVariables } from "./stable-deps";
import type { Row } from "./resource-result";
import type { DocumentData, DocumentVariables } from "./typed-document";

type AuthoredDocument = TypedDocumentNode<unknown, any>;
type AuthoredVariables<TDocument extends AuthoredDocument> =
  DocumentVariables<TDocument> extends Record<string, unknown>
    ? DocumentVariables<TDocument>
    : Record<string, never>;

export interface AuthoredQueryOptions {
  enabled?: boolean;
  /** Model labels this bespoke read depends on; local writes and live changes refetch it. */
  models?: readonly string[];
}

export interface AuthoredQueryResult<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export type AuthoredStringIdRow = Row & { id: string };

export interface AuthoredRowsOptions<
  TDocument extends AuthoredDocument,
  TRow extends AuthoredStringIdRow,
> extends AuthoredQueryOptions {
  variables?: AuthoredVariables<TDocument>;
  selectRows: (data: DocumentData<TDocument> | undefined) => readonly TRow[];
}

export interface AuthoredRowsResult<
  TData,
  TRow extends AuthoredStringIdRow,
> extends AuthoredQueryResult<TData> {
  rows: readonly TRow[];
}

/** Run a generated authored query document — the escape hatch for bespoke reads. */
export function useAuthoredQuery<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  options: AuthoredQueryOptions = {},
): AuthoredQueryResult<DocumentData<TDocument>> {
  const stable = useStableVariables(variables);
  const enabled = options.enabled ?? true;
  const models = useStableArray(options.models ?? []);
  const run = useDocumentQuery(document, stable, enabled);
  useRegisterModelsRefetch(models, run.refetch, enabled);
  return {
    data: run.data as DocumentData<TDocument> | undefined,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

/** Run an authored query and project its bespoke result into id-bearing rows. */
export function useAuthoredRows<
  TDocument extends AuthoredDocument,
  TRow extends AuthoredStringIdRow,
>(
  document: TDocument,
  options: AuthoredRowsOptions<TDocument, TRow>,
): AuthoredRowsResult<DocumentData<TDocument>, TRow> {
  const { variables, selectRows, ...queryOptions } = options;
  const query = useAuthoredQuery(document, variables, queryOptions);
  const rows = useMemo(
    () => selectRows(query.data),
    [query.data, selectRows],
  );
  return { ...query, rows };
}

export type AuthoredMutate<TDocument extends AuthoredDocument> = (
  variables?: AuthoredVariables<TDocument>,
) => Promise<DocumentData<TDocument> | undefined>;

export interface AuthoredMutationOptions<
  TData = unknown,
  TVariables = Record<string, unknown>,
> {
  /** Models whose registered reads should refetch after this mutation succeeds. */
  invalidateModels?: readonly string[];
  /** Optional domain-level success guard before invalidating registered reads. */
  shouldInvalidate?: (data: TData | undefined, variables: TVariables) => boolean;
}

/** Run a generated authored mutation document; the runner throws on GraphQL error. */
export function useAuthoredMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
  options: AuthoredMutationOptions<
    DocumentData<TDocument>,
    AuthoredVariables<TDocument>
  > = {},
): [AuthoredMutate<TDocument>, { fetching: boolean; error: Error | null }] {
  type Data = DocumentData<TDocument>;
  type Variables = AuthoredVariables<TDocument>;
  const { execute, fetching, error } = useDocumentMutation<Data, Variables>(document);
  const invalidateModelLabels = useStableArray(options.invalidateModels ?? []);
  const invalidateModels = useInvalidateModels();
  const shouldInvalidate = options.shouldInvalidate;
  const mutate = useCallback<AuthoredMutate<TDocument>>(
    async (variables) => {
      const resolvedVariables = (variables ?? {}) as Variables;
      const data = await execute(resolvedVariables);
      if (
        invalidateModelLabels.length > 0
        && (shouldInvalidate?.(data, resolvedVariables) ?? true)
      ) {
        invalidateModels(invalidateModelLabels);
      }
      return data;
    },
    [execute, invalidateModelLabels, invalidateModels, shouldInvalidate],
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
