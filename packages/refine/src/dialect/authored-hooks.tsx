import { useCallback, useMemo, useRef } from "react";
import {
  useCustom,
  useCustomMutation,
  useInvalidate,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import { useQueryClient } from "@tanstack/react-query";

import {
  authoredQueryMeta,
  authoredQueryReadsAnyModel,
} from "../query-invalidation";
import {
  useStableArray,
  useStableVariables,
} from "../stable-deps";
import type {
  DocumentData,
  DocumentVariables,
  TypedDocumentNode,
} from "../typed-document";
import { useActiveDataProviderName } from "./data-provider-context";
import { mutationMeta, queryMeta } from "./wire";

/** Any authored (non-CRUD) GraphQL operation: a generated `TypedDocumentNode`. */
export type AuthoredDocument = TypedDocumentNode<unknown, any>;
/**
 * The variables an authored document takes, or `Record<string, never>` when it
 * takes none — the parameter type the authored hooks require and the type a
 * caller composing them (e.g. a source that maps its own input to a document's
 * variables) declares, so the variables stay pinned to the document.
 */
export type AuthoredVariables<TDocument extends AuthoredDocument> =
  DocumentVariables<TDocument> extends Record<string, unknown>
    ? DocumentVariables<TDocument>
    : Record<string, never>;
type InvalidateParams = Parameters<ReturnType<typeof useInvalidate>>[0];

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
  const activeDataProviderName = useActiveDataProviderName();
  const dataProviderName = options.dataProviderName ?? activeDataProviderName ?? "default";
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
  /** Resource invalidations prepared by the caller that owns resource metadata. */
  invalidates?: readonly InvalidateParams[];
  /** Optional domain-level success guard before invalidating registered reads. */
  shouldInvalidate?: (data: TData | undefined, variables: TVariables) => boolean;
  /**
   * Extract a domain result envelope from successful GraphQL transport data.
   * If it carries `{ error_code, error }`, the hook throws before invalidating
   * reads so callers do not each re-implement the same result gating.
   */
  errorFrom?: (
    data: TData | undefined,
    variables: TVariables,
  ) => AuthoredMutationEnvelope | Error | string | null | undefined;
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
  const activeDataProviderName = useActiveDataProviderName();
  const dataProviderName = options.dataProviderName ?? activeDataProviderName ?? "default";
  const run = useCustomMutation<BaseRecord, HttpError, Variables>();
  const invalidateModelLabels = useStableArray(options.invalidateModels ?? []);
  const invalidates = options.invalidates ?? EMPTY_INVALIDATIONS;
  const invalidate = useInvalidate();
  const queryClient = useQueryClient();
  const shouldInvalidate = options.shouldInvalidate;
  const errorFrom = options.errorFrom;
  // Stable identity: chat runtimes and other long-lived effects may depend on
  // authored mutations, while refine can churn `mutateAsync` across renders.
  // Read the latest execution context at call time so consumers do not reconnect
  // or restart work just because the hook rerendered.
  const mutationRef = useRef({
    dataProviderName,
    document,
    invalidate,
    invalidateModelLabels,
    invalidates,
    mutateAsync: run.mutateAsync,
    queryClient,
    shouldInvalidate,
    errorFrom,
  });
  mutationRef.current = {
    dataProviderName,
    document,
    invalidate,
    invalidateModelLabels,
    invalidates,
    mutateAsync: run.mutateAsync,
    queryClient,
    shouldInvalidate,
    errorFrom,
  };
  const mutate = useCallback<AuthoredMutate<TDocument>>(async (variables) => {
    const {
      dataProviderName,
      document,
      invalidate,
      invalidateModelLabels,
      invalidates,
      mutateAsync,
      queryClient,
      shouldInvalidate,
      errorFrom,
    } = mutationRef.current;
    const resolvedVariables = (variables ?? {}) as Variables;
    const response = await mutateAsync({
      url: "",
      method: "post",
      values: resolvedVariables,
      dataProviderName,
      meta: mutationMeta(document, resolvedVariables),
    });
    const data = authoredOperationData<Data>(response.data);
    const resultError = errorFromAuthoredEnvelope(
      errorFrom?.(data, resolvedVariables),
    );
    if (resultError) throw resultError;
    if (
      (invalidateModelLabels.length > 0 || invalidates.length > 0)
      && (shouldInvalidate?.(data, resolvedVariables) ?? true)
    ) {
      await Promise.all([
        ...invalidates.map((target) => invalidate(target)),
        ...(invalidateModelLabels.length > 0
          ? [
              queryClient.invalidateQueries({
                predicate: (query) =>
                  authoredQueryReadsAnyModel(query.meta, invalidateModelLabels),
                type: "all",
                refetchType: "active",
              }),
            ]
          : []),
      ]);
    }
    return data;
  }, []);
  return [
    mutate,
    {
      fetching: run.mutation.isPending,
      error: run.mutation.error as Error | null,
    },
  ];
}

export interface AuthoredMutationEnvelope {
  error?: unknown;
  error_code?: unknown;
}

export function errorFromAuthoredEnvelope(
  value: AuthoredMutationEnvelope | Error | string | null | undefined,
): Error | null {
  if (!value) return null;
  if (value instanceof Error) return value;
  if (typeof value === "string") return value ? new Error(value) : null;
  if (!value.error_code) return null;
  const message = typeof value.error === "string" && value.error
    ? value.error
    : String(value.error_code);
  return new Error(message);
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

const EMPTY_INVALIDATIONS: readonly InvalidateParams[] = [];
