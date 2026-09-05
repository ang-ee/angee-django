import { useCallback, useMemo, useRef } from "react";
import {
  useCustomMutation,
  useDataProvider,
  useInvalidate,
  useSubscription,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import {
  useQuery,
  useInfiniteQuery,
  useQueries,
  useQueryClient,
  type InfiniteData,
  type UseQueryResult,
  type UseInfiniteQueryResult,
  hashKey,
} from "@tanstack/react-query";

import {
  invalidateAuthoredQueries,
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
import { authoredOperationData, mutationMeta } from "./wire";
import { authoredInfiniteQueryOptions, authoredQueryOptions, useAuthoredErrorPolicy } from "./authored-query-options";
export { authoredQueryKey, authoredQueryOptions, authoredInfiniteQueryOptions } from "./authored-query-options";
export { authoredOperationData } from "./wire";

/** Any authored (non-CRUD) GraphQL operation: a generated `TypedDocumentNode`. */
export type AuthoredDocument = TypedDocumentNode<
  unknown,
  never
>;
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
  /**
   * Exact canonical model labels this bespoke read depends on; local writes and
   * live changes refetch it. This metadata-free package does no alias resolution:
   * a rendered caller accepts friendly spellings only by canonicalizing at its
   * `@angee/metadata` edge before calling this hook.
   */
  models?: readonly string[];
}

/** One authored read in a dynamic batch, addressed by a caller-stable key. */
export interface AuthoredQueryBatchScope<TDocument extends AuthoredDocument> {
  /** Result label only; document/provider/variables own query identity. */
  key: string;
  document: TDocument;
  variables?: AuthoredVariables<TDocument>;
  /** Canonical model labels whose changes invalidate this read. */
  models?: readonly string[];
}

export type AuthoredInfinitePageVariables<
  TDocument extends AuthoredDocument,
> = Partial<AuthoredVariables<TDocument>>;

export interface AuthoredInfiniteQueryOptions<
  TDocument extends AuthoredDocument,
  TRow,
  TPageVariables extends AuthoredInfinitePageVariables<TDocument>,
> extends AuthoredQueryOptions {
  /** Read the keyset page rows from one authored operation result. */
  getRows: (data: DocumentData<TDocument>) => readonly TRow[];
  /** Stable identity for page-level dedupe. */
  getRowId: (row: TRow) => string;
  /**
   * Return cursor variables for the next older page. Returning `undefined`
   * marks the infinite read exhausted.
   */
  getPageParam: (
    lastPageRows: readonly TRow[],
    lastPage: DocumentData<TDocument>,
  ) => TPageVariables | undefined;
}

export function useAuthoredQuery<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  options: AuthoredQueryOptions = {},
): UseQueryResult<DocumentData<TDocument>, Error> {
  const activeProvider = useActiveDataProviderName();
  const provider = options.dataProviderName ?? activeProvider ?? "default";
  const dataProvider = useDataProvider();
  const client = useQueryClient();
  const models = useStableArray(options.models ?? []);
  const configured = authoredQueryOptions(client, dataProvider, provider, document, variables, models);
  const result = useQuery({
    ...configured, enabled: options.enabled ?? true,
  });
  useAuthoredLiveInterest(options.enabled ?? true, models);
  useAuthoredErrorPolicy([configured.queryKey]);
  return result;
}

/** Native query results addressed by a domain label; labels never key the cache. */
export function useAuthoredQueryBatch<TDocument extends AuthoredDocument>(
  scopes: readonly AuthoredQueryBatchScope<TDocument>[],
  options: AuthoredOperationOptions & { enabled?: boolean } = {},
): ReadonlyMap<string, UseQueryResult<DocumentData<TDocument>, Error>> {
  const activeProvider = useActiveDataProviderName();
  const provider = options.dataProviderName ?? activeProvider ?? "default";
  const dataProvider = useDataProvider();
  const client = useQueryClient();
  const queries = scopes.map((scope) => {
    const configured = authoredQueryOptions(client, dataProvider, provider, scope.document, scope.variables, scope.models);
    return {
      ...configured, enabled: options.enabled ?? true,
    };
  });
  const results = useQueries({ queries });
  const models = useStableArray([...new Set(scopes.flatMap((scope) => scope.models ?? []))].sort());
  useAuthoredLiveInterest(options.enabled ?? true, models);
  useAuthoredErrorPolicy(queries.map((query) => query.queryKey));
  return new Map(scopes.map((scope, index) => [scope.key, results[index]!]));
}

/** Invalidate every authored read registered against any supplied model. */
export function useInvalidateAuthoredModels(): (
  modelLabels: readonly string[],
) => void {
  const queryClient = useQueryClient();
  return useCallback((modelLabels: readonly string[]) => {
    void invalidateAuthoredQueries(queryClient, modelLabels);
  }, [queryClient]);
}

export function useAuthoredInfiniteQuery<
  TDocument extends AuthoredDocument,
  TRow,
  TPageVariables extends AuthoredInfinitePageVariables<TDocument> =
    AuthoredInfinitePageVariables<TDocument>,
>(
  document: TDocument,
  variables: AuthoredVariables<TDocument>,
  options: AuthoredInfiniteQueryOptions<TDocument, TRow, TPageVariables>,
): UseInfiniteQueryResult<InfiniteData<DocumentData<TDocument>, TPageVariables | null>, Error> & { rows: readonly TRow[] } {
  type Data = DocumentData<TDocument>;
  const client = useQueryClient();

  const stable = useStableVariables(variables);
  const enabled = options.enabled ?? true;
  const models = useStableArray(options.models ?? []);
  const activeDataProviderName = useActiveDataProviderName();
  const dataProviderName = options.dataProviderName ?? activeDataProviderName ?? "default";
  const dataProvider = useDataProvider();
  const callbacksRef = useRef<{
    getRows: (data: Data) => readonly TRow[];
    getRowId: (row: TRow) => string;
    getPageParam: (
      lastPageRows: readonly TRow[],
      lastPage: Data,
    ) => TPageVariables | undefined;
  }>({
    getRows: options.getRows,
    getRowId: options.getRowId,
    getPageParam: options.getPageParam,
  });
  callbacksRef.current = {
    getRows: options.getRows,
    getRowId: options.getRowId,
    getPageParam: options.getPageParam,
  };
  const configured = authoredInfiniteQueryOptions<TDocument, TPageVariables>(
    client, dataProvider, dataProviderName, document, stable as AuthoredVariables<TDocument>,
    (lastPage, allPages) => {
      const { getRows, getRowId, getPageParam } = callbacksRef.current;
      const rows = getRows(lastPage);
      if (!rows.length || lastPageOnlyRepeatsPreviousRows(allPages, getRows, getRowId)) return undefined;
      return getPageParam(rows, lastPage);
    },
    models,
  );
  const queryKey = hashKey(configured.queryKey);
  const query = useInfiniteQuery({
    ...configured, enabled,
  });
  useAuthoredErrorPolicy([configured.queryKey]);
  useAuthoredLiveInterest(enabled, models);

  const archiveRef = useRef<{
    queryKey: string | null;
    byId: Map<string, TRow>;
    rows: readonly TRow[];
  }>({
    queryKey: null,
    byId: new Map<string, TRow>(),
    rows: [],
  });
  if (archiveRef.current.queryKey !== queryKey) {
    archiveRef.current = {
      queryKey,
      byId: new Map<string, TRow>(),
      rows: [],
    };
  }
  const rows = useMemo(
    () => accumulateAuthoredInfiniteRows(
      archiveRef.current,
      query.data?.pages ?? [],
      callbacksRef.current.getRows,
      callbacksRef.current.getRowId,
    ),
    [query.data, queryKey],
  );
  // Temporary retention projection: the domain cursor/revocation protocol cannot
  // yet prove archive deletion safe (see docs/frontend/upstream-reuse.md).
  // All lifecycle controls come directly from the native infinite result.
  return { ...query, rows };
}

export type AuthoredMutate<TDocument extends AuthoredDocument> = (
  variables?: AuthoredVariables<TDocument>,
) => Promise<DocumentData<TDocument> | undefined>;

export interface AuthoredMutationOptions<
  TData = unknown,
  TVariables = Record<string, unknown>,
> extends AuthoredOperationOptions {
  /**
   * Exact canonical model labels whose registered reads should refetch after
   * success. This metadata-free package exact-matches the strings; callers that
   * accept aliases canonicalize them before this boundary.
   */
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
              invalidateAuthoredQueries(queryClient, invalidateModelLabels),
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

const EMPTY_INVALIDATIONS: readonly InvalidateParams[] = [];

/** Live invalidation rides the provider's change consumer, so the hook's own event handler is a noop. */
const NO_LIVE_EVENT = (): void => undefined;

/** A stable per-model-set channel for the authored read's live subscription. */
function authoredLiveChannel(models: readonly string[]): string {
  return `angee/authored/${models.join(",")}`;
}

function useAuthoredLiveInterest(
  enabled: boolean,
  models: readonly string[],
): void {
  // An authored read is invisible to the live bridge unless it declares interest:
  // a page built only from authored queries would open no socket and never go
  // live. Register each read model so its changes root is subscribed through the
  // shared fan-out (one upstream per root, joined with any resource hook watching
  // the same model) and torn down with the hook. refine's useSubscription owns
  // the lifecycle and no-ops when the app has no live provider; the provider's
  // consumer invalidates this read on each change, so onLiveEvent stays a noop.
  useSubscription({
    channel: authoredLiveChannel(models),
    params: { models },
    types: ["*"],
    enabled: enabled && models.length > 0,
    onLiveEvent: NO_LIVE_EVENT,
  });
}

function accumulateAuthoredInfiniteRows<TRow, TData>(
  archive: {
    byId: Map<string, TRow>;
    rows: readonly TRow[];
  },
  pages: readonly TData[],
  getRows: (data: TData) => readonly TRow[],
  getRowId: (row: TRow) => string,
): readonly TRow[] {
  let changed = false;
  for (const page of pages) {
    for (const row of getRows(page)) {
      const id = getRowId(row);
      if (archive.byId.get(id) !== row) {
        archive.byId.set(id, row);
        changed = true;
      }
    }
  }
  if (changed) archive.rows = [...archive.byId.values()];
  return archive.rows;
}

function lastPageOnlyRepeatsPreviousRows<TRow, TData>(
  pages: readonly TData[],
  getRows: (data: TData) => readonly TRow[],
  getRowId: (row: TRow) => string,
): boolean {
  if (pages.length < 2) return false;
  const previousIds = new Set<string>();
  for (const page of pages.slice(0, -1)) {
    for (const row of getRows(page)) previousIds.add(getRowId(row));
  }
  const lastPage = pages.at(-1);
  if (lastPage === undefined) return false;
  const lastRows = getRows(lastPage);
  return lastRows.length > 0 &&
    lastRows.every((row) => previousIds.has(getRowId(row)));
}
