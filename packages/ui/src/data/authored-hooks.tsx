import { useCallback, useMemo, useRef } from "react";
import {
  useCustom,
  useCustomMutation,
  useInvalidate,
  type BaseRecord,
  type HttpError,
  type MetaQuery,
} from "@refinedev/core";
import { useQueryClient } from "@tanstack/react-query";

import {
  useActiveGraphQLSchemaName,
  useSchemaFieldMetadata,
  refineInvalidationParams,
  resourceInvalidationTargets,
} from "@angee/resources";
import {
  authoredQueryMeta,
  authoredQueryReadsAnyModel,
  useStableArray,
  useStableVariables,
} from "@angee/refine";
import type {
  DocumentData,
  DocumentVariables,
  TypedDocumentNode,
} from "@angee/refine";

type AuthoredDocument = TypedDocumentNode<unknown, any>;
type AuthoredVariables<TDocument extends AuthoredDocument> =
  DocumentVariables<TDocument> extends Record<string, unknown>
    ? DocumentVariables<TDocument>
    : Record<string, never>;

export interface AuthoredOperationOptions {
  /** Refine data provider name; defaults to the active Angee layout schema. */
  dataProviderName?: string;
}

export interface AuthoredQueryOptions extends AuthoredOperationOptions {
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

export function useAuthoredQuery<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  options: AuthoredQueryOptions = {},
): AuthoredQueryResult<DocumentData<TDocument>> {
  const stable = useStableVariables(variables);
  const enabled = options.enabled ?? true;
  const models = useStableArray(options.models ?? []);
  const activeSchema = useActiveGraphQLSchemaName();
  const dataProviderName = options.dataProviderName ?? activeSchema ?? "default";
  const meta = useMemo(
    () => queryMeta(document, stable),
    [document, stable],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName,
    meta,
    queryOptions: {
      enabled,
      meta: authoredQueryMeta(models),
    },
  });
  // Stable identity: callers (e.g. the operator token-refresh interval) put
  // `refetch` in effect deps, and react-query churns `run.query`'s identity on
  // every state change — depending on it would tear those effects down.
  const queryRef = useRef(run.query);
  queryRef.current = run.query;
  const refetch = useCallback(() => {
    void queryRef.current.refetch();
  }, []);
  const data = authoredQueryData(run.query.data);
  return {
    data: data as DocumentData<TDocument> | undefined,
    fetching: run.query.isFetching,
    error: run.query.error as Error | null,
    refetch,
  };
}

export type AuthoredMutate<TDocument extends AuthoredDocument> = (
  variables?: AuthoredVariables<TDocument>,
) => Promise<DocumentData<TDocument> | undefined>;

export interface AuthoredMutationOptions<
  TData = unknown,
  TVariables = Record<string, unknown>,
> extends AuthoredOperationOptions {
  /** Models whose registered reads should refetch after this mutation succeeds. */
  invalidateModels?: readonly string[];
  /** Optional domain-level success guard before invalidating registered reads. */
  shouldInvalidate?: (data: TData | undefined, variables: TVariables) => boolean;
}

export function useAuthoredMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
  options: AuthoredMutationOptions<
    DocumentData<TDocument>,
    AuthoredVariables<TDocument>
  > = {},
): [AuthoredMutate<TDocument>, { fetching: boolean; error: Error | null }] {
  type Data = DocumentData<TDocument>;
  type Variables = AuthoredVariables<TDocument>;
  const activeSchema = useActiveGraphQLSchemaName();
  const dataProviderName = options.dataProviderName ?? activeSchema ?? "default";
  const run = useCustomMutation<BaseRecord, HttpError, Variables>();
  const invalidateModelLabels = useStableArray(options.invalidateModels ?? []);
  const schemaMetadata = useSchemaFieldMetadata();
  const invalidate = useInvalidate();
  const queryClient = useQueryClient();
  const invalidationTargets = useMemo(
    () => resourceInvalidationTargets(schemaMetadata, invalidateModelLabels),
    [schemaMetadata, invalidateModelLabels],
  );
  const shouldInvalidate = options.shouldInvalidate;
  const mutate = useCallback<AuthoredMutate<TDocument>>(
    async (variables) => {
      const resolvedVariables = (variables ?? {}) as Variables;
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: resolvedVariables,
        dataProviderName,
        meta: mutationMeta(document, resolvedVariables),
      });
      const data = authoredOperationData<Data>(response.data);
      if (
        invalidateModelLabels.length > 0
        && (shouldInvalidate?.(data, resolvedVariables) ?? true)
      ) {
        await Promise.all([
          ...invalidationTargets.map((target) =>
            invalidate(refineInvalidationParams(target)),
          ),
          queryClient.invalidateQueries({
            predicate: (query) =>
              authoredQueryReadsAnyModel(query.meta, invalidateModelLabels),
            type: "all",
            refetchType: "active",
          }),
        ]);
      }
      return data;
    },
    [
      dataProviderName,
      document,
      invalidateModelLabels,
      invalidate,
      invalidationTargets,
      queryClient,
      run.mutateAsync,
      shouldInvalidate,
    ],
  );
  return [
    mutate,
    {
      fetching: run.mutation.isPending,
      error: run.mutation.error as Error | null,
    },
  ];
}

export function authoredOperationData<TData>(payload: unknown): TData | undefined {
  if (isGraphQLResponseEnvelope(payload)) {
    return payload.data as TData | undefined;
  }
  return payload as TData | undefined;
}

export function authoredQueryData<TData>(
  response: { data?: unknown } | undefined,
): TData | undefined {
  if (!response) return undefined;
  return authoredOperationData<TData>(response.data);
}

function isGraphQLResponseEnvelope(
  payload: unknown,
): payload is { data?: unknown; errors?: unknown; extensions?: unknown } {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  const keys = Object.keys(payload);
  return keys.includes("data") &&
    keys.every((key) => key === "data" || key === "errors" || key === "extensions");
}

function queryMeta(
  gqlQuery: AuthoredDocument,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlQuery, gqlVariables } as unknown as MetaQuery;
}

function mutationMeta(
  gqlMutation: AuthoredDocument,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlMutation, gqlVariables } as unknown as MetaQuery;
}
