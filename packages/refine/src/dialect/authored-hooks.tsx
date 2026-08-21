import { useCallback, useMemo, useRef } from "react";
import {
  useCustom,
  useCustomMutation,
  useDataProvider,
  useInvalidate,
  useSubscription,
  type BaseRecord,
  type CustomResponse,
  type HttpError,
} from "@refinedev/core";
import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
  type QueryFunctionContext,
} from "@tanstack/react-query";

import {
  authoredQueryMeta,
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
import { mutationMeta, queryMeta } from "./wire";

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

export interface AuthoredQueryResult<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
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

export interface AuthoredInfiniteQueryResult<TData, TRow> {
  rows: readonly TRow[];
  pages: readonly TData[];
  fetching: boolean;
  fetchingOlder: boolean;
  error: Error | null;
  hasMore: boolean;
  fetchOlder: () => void;
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
  useAuthoredLiveInterest(enabled, models);
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

export function useAuthoredInfiniteQuery<
  TDocument extends AuthoredDocument,
  TRow,
  TPageVariables extends AuthoredInfinitePageVariables<TDocument> =
    AuthoredInfinitePageVariables<TDocument>,
>(
  document: TDocument,
  variables: AuthoredVariables<TDocument>,
  options: AuthoredInfiniteQueryOptions<TDocument, TRow, TPageVariables>,
): AuthoredInfiniteQueryResult<DocumentData<TDocument>, TRow> {
  type Data = DocumentData<TDocument>;
  type Variables = AuthoredVariables<TDocument>;
  type PageParam = TPageVariables | null;
  type QueryKey = readonly [
    "angee",
    "authored",
    "infinite",
    string,
    TDocument,
    Variables,
  ];

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
  const queryKey = useMemo<QueryKey>(
    () => [
      "angee",
      "authored",
      "infinite",
      dataProviderName,
      document,
      stable,
    ],
    [dataProviderName, document, stable],
  );
  const custom = dataProvider(dataProviderName).custom;
  const query = useInfiniteQuery<Data, Error, InfiniteData<Data, PageParam>, QueryKey, PageParam>({
    queryKey,
    queryFn: async ({
      pageParam,
    }: QueryFunctionContext<QueryKey, PageParam>) => {
      if (!custom) {
        throw new Error(
          `Data provider "${dataProviderName}" does not support custom authored queries.`,
        );
      }
      const pageVariables = {
        ...stable,
        ...(pageParam ?? {}),
      } as Variables;
      const response: CustomResponse<BaseRecord> =
        await custom<BaseRecord, Record<string, unknown>, Record<string, unknown>>({
          url: "",
          method: "post",
          meta: queryMeta(document, pageVariables),
        });
      const data = authoredOperationData<Data>(response.data);
      if (data === undefined) {
        throw new Error("Authored infinite query returned no data.");
      }
      return data;
    },
    initialPageParam: null,
    getNextPageParam: (lastPage, allPages) => {
      const { getRows, getRowId, getPageParam } = callbacksRef.current;
      const lastPageRows = getRows(lastPage);
      if (lastPageRows.length === 0) return undefined;
      if (lastPageOnlyRepeatsPreviousRows(allPages, getRows, getRowId)) {
        return undefined;
      }
      return getPageParam(lastPageRows, lastPage) ?? undefined;
    },
    placeholderData: () => undefined,
    enabled,
    meta: authoredQueryMeta(models),
  });
  useAuthoredLiveInterest(enabled, models);

  const archiveRef = useRef<{
    queryKey: QueryKey | null;
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
  const pages = query.data?.pages ?? (EMPTY_AUTHORED_INFINITE_PAGES as readonly Data[]);
  const queryRef = useRef(query);
  queryRef.current = query;
  const fetchOlder = useCallback(() => {
    const current = queryRef.current;
    if (!current.hasNextPage || current.isFetchingNextPage) return;
    void current.fetchNextPage();
  }, []);
  const refetch = useCallback(() => {
    void queryRef.current.refetch();
  }, []);

  return {
    rows,
    pages,
    fetching: query.isFetching,
    fetchingOlder: query.isFetchingNextPage,
    error: query.error,
    hasMore: query.hasNextPage,
    fetchOlder,
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

const EMPTY_AUTHORED_INFINITE_PAGES: readonly unknown[] = [];

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
