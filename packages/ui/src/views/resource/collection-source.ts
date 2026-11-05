import * as React from "react";
import {
  useCanonicalResourceModelLabels,
  type ResourceQuery,
  type Row,
} from "@angee/metadata";
import {
  groupedListQueryMeta,
  stableSerialize,
  useAuthoredQueryBatch,
  type AngeeListBatchEntry,
  type AngeeListBatchScope,
  type AuthoredDocument,
  type AuthoredVariables,
  type DocumentData,
  type GroupByResult,
  type ListBatchTarget,
  type TypedDocumentNode,
} from "@angee/refine";
import type {
  ResourceListOrder,
  ResourceViewFilter,
  ResourceViewGroup,
} from "./resource-view-model";

/** A server page request in the collection's semantic query language. */
export interface CollectionPageRequest {
  /** Readable parent identity for an authored hierarchy's child page. */
  parentId?: string;
  filter: ResourceViewFilter | undefined;
  order: ResourceListOrder | undefined;
  page: number;
  pageSize: number;
}

/** Fetch expanded native leaf pages together, isolating failed aliases on retry. */
export function useGroupedNativePages(
  target: ListBatchTarget | null,
  scopes: readonly AngeeListBatchScope[],
  fields: readonly string[],
  modelLabel: string | undefined,
): ReadonlyMap<string, AngeeListBatchEntry> {
  const models = useCanonicalResourceModelLabels(modelLabel ? [modelLabel] : []);
  const targetKey = stableSerialize(target);
  const scopesKey = stableSerialize(scopes);
  const fieldsKey = stableSerialize(fields);
  const request = React.useMemo(
    () => target && scopes.length > 0 ? groupedListQueryMeta(target, fields, scopes) : null,
    [targetKey, scopesKey, fieldsKey],
  );
  const results = useAuthoredQueryBatch(request ? [{
    key: "grouped-leaves",
    document: request.document,
    variables: request.variables,
    models,
  }] : [], { dataProviderName: target?.dataProviderName, errorNotification: false });
  const result = results.get("grouped-leaves");
  // A failed shared document cannot identify which alias failed. Retry only on
  // that path so healthy buckets remain visible beside a failed leaf.
  const fallbackRequests = React.useMemo(() => {
    if (!result?.error || !target) return [];
    return scopes.map((scope) => ({
      key: scope.key,
      ...groupedListQueryMeta(target, fields, [scope]),
      models,
    }));
  }, [result?.error, targetKey, scopesKey, fieldsKey, models]);
  const fallback = useAuthoredQueryBatch(fallbackRequests, {
    dataProviderName: target?.dataProviderName,
    errorNotification: false,
  });
  const refetchBatch = React.useCallback(() => { void result?.refetch(); }, [result?.refetch]);
  return React.useMemo(() => new Map(scopes.map((scope, index) => {
    const entry = result?.error ? fallback.get(scope.key) : result;
    const data = entry?.data;
    const alias = result?.error ? 0 : index;
    const aggregate = data?.[`group_${alias}_aggregate`] as
      | { aggregate?: { count?: number } }
      | undefined;
    return [scope.key, {
      rows: (data?.[`group_${alias}_rows`] ?? []) as readonly Row[],
      total: aggregate?.aggregate?.count,
      fetching: entry?.isFetching ?? Boolean(result?.error),
      error: entry?.error ?? null,
      refetch: result?.error ? () => { void entry?.refetch(); } : refetchBatch,
    }];
  })), [scopesKey, result, fallback, refetchBatch]);
}

export interface CollectionGroupRequest extends CollectionPageRequest {
  group: ResourceViewGroup;
}

export interface CollectionPage<TRow extends Row> {
  rows: readonly TRow[];
  total: number;
  /** Server-owned count caption, including the projection's actual count units. */
  summary?: string;
}

/** Authored transport; the list still owns filtering, grouping and pagination. */
export type CollectionQuery<
  TDocument extends AuthoredDocument,
  TRequest,
  TResult,
> = {
  models?: readonly string[];
  dataProviderName?: string;
  enabled?: boolean;
} & ({
  document: TDocument;
  variables: (request: TRequest) => AuthoredVariables<TDocument>;
  select: (data: DocumentData<TDocument>) => TResult;
  batch?: never;
} | {
  /** A batch-only transport has no unreachable single-page document. */
  batch: AnyCollectionBatchQuery<TRequest, TResult>;
  document?: never;
  variables?: never;
  select?: never;
});

export interface CollectionBatchPage<TResult> {
  data?: TResult;
  error?: Error | null;
}

export interface CollectionBatchQuery<TDocument extends AuthoredDocument, TRequest, TStored, TResult = TStored> {
  document: TDocument;
  maxPages?: number;
  variables: (requests: readonly (TRequest & { key: string })[]) => AuthoredVariables<TDocument>;
  select: (data: DocumentData<TDocument>, requests: readonly (TRequest & { key: string })[]) => ReadonlyMap<string, CollectionBatchPage<TStored>>;
  /** Project cached page data with the current render's presentation context. */
  present?: (data: TStored) => TResult;
}

export type AnyCollectionBatchQuery<TRequest, TResult> = Omit<
  CollectionBatchQuery<TypedDocumentNode<unknown, Record<string, unknown>>, TRequest, TResult>,
  "present"
> & {
  /** The declaration helper checks the stored type before this erased boundary. */
  present?: (data: unknown) => TResult;
};

/**
 * Preserve authored batch variable/result types at the declaration.
 *
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function collectionBatchQuery<TDocument extends AuthoredDocument, TRequest, TStored, TResult = TStored>(
  batch: CollectionBatchQuery<TDocument, TRequest, TStored, TResult>,
): AnyCollectionBatchQuery<TRequest, TResult> {
  return batch as unknown as AnyCollectionBatchQuery<TRequest, TResult>;
}

export type AnyCollectionQuery<TRequest, TResult> = CollectionQuery<
  TypedDocumentNode<unknown, Record<string, unknown>>,
  TRequest,
  TResult
>;

/** Keep document variables/results checked at the declaration, erase once here.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function collectionQuery<
  TDocument extends AuthoredDocument,
  TRequest,
  TResult,
>(
  source: CollectionQuery<TDocument, TRequest, TResult>,
): AnyCollectionQuery<TRequest, TResult> {
  return source as unknown as AnyCollectionQuery<TRequest, TResult>;
}

export interface CollectionSource<TRow extends Row> {
  query: ResourceQuery;
  rows: AnyCollectionQuery<CollectionPageRequest, CollectionPage<TRow>>;
  groups?: AnyCollectionQuery<
    CollectionGroupRequest,
    GroupByResult & { summary?: string }
  >;
  /** Initial size of each independently paged expanded group. */
  leafPageSize?: number;
}

interface CollectionQueryResult<TResult> {
  data: TResult | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Use the authored-query owner for a dynamic frontier of group or row requests. */
export function useCollectionQueryBatch<TRequest extends { page: number }, TResult>(
  source: AnyCollectionQuery<TRequest, TResult> | undefined,
  requests: readonly (TRequest & { key: string })[],
): ReadonlyMap<string, CollectionQueryResult<TResult>> {
  const models = useCanonicalResourceModelLabels(source?.models);
  const single = source?.document ? source : undefined;
  const batch = source?.batch;
  const prepared = React.useMemo(() => {
    const errors = new Map<string, Error>();
    const scopes = single
      ? requests.flatMap((request) => {
          try {
            return [
              {
                key: request.key,
                document: single.document,
                variables: single.variables(request),
                models,
              },
            ];
          } catch (error) {
            errors.set(
              request.key,
              error instanceof Error ? error : new Error(String(error)),
            );
            return [];
          }
        })
      : [];
    return { errors, scopes };
  }, [single, requests, models]);
  const batchPrepared = React.useMemo(() => {
    const errors = new Map<string, Error>();
    const scopes = batch ? requests.flatMap((request) => {
      try {
        return [{ key: request.key, document: batch.document,
          variables: batch.variables([request]),
          placeholderKey: JSON.stringify(batch.variables([{ ...request, page: 1 }])), models }];
      } catch (error) {
        errors.set(request.key, error instanceof Error ? error : new Error(String(error)));
        return [];
      }
    }) : [];
    return { errors, scopes };
  }, [batch, requests, models]);
  const results = useAuthoredQueryBatch(prepared.scopes, {
    dataProviderName: source?.dataProviderName,
    enabled: source?.enabled,
  });
  const byKey = new Map(requests.map((request) => [request.key, request]));
  const batchResults = useAuthoredQueryBatch<
    TypedDocumentNode<unknown, Record<string, unknown>>, CollectionBatchPage<TResult>
  >(batchPrepared.scopes, {
    dataProviderName: source?.dataProviderName,
    enabled: source?.enabled,
    errorNotification: false,
  }, batch ? {
    maxPages: batch.maxPages,
    variables: (scopes) => batch.variables(scopes.map((scope) => byKey.get(scope.key)!)),
    select: (data, scopes) => batch.select(data, scopes.map((scope) => byKey.get(scope.key)!)),
  } : undefined);
  return React.useMemo(() => {
    const normalized = new Map<string, CollectionQueryResult<TResult>>(
      [...results].map(([key, result]) => [
        key,
        {
          data: result.data === undefined ? undefined : single?.select(result.data),
          fetching: result.isFetching,
          error: result.error,
          refetch: () => {
            void result.refetch();
          },
        },
      ]),
    );
    prepared.errors.forEach((error, key) =>
      normalized.set(key, {
        data: undefined,
        fetching: false,
        error,
        refetch: () => {},
      }),
    );
    if (batch) {
      requests.forEach((request) => {
        const result = batchResults.get(request.key);
        const page = result?.data;
        normalized.set(request.key, {
          data: page?.data === undefined ? undefined : batch.present?.(page.data) ?? page.data,
          fetching: result?.isFetching ?? false,
          error: batchPrepared.errors.get(request.key) ?? page?.error ?? result?.error ?? null,
          refetch: () => { void result?.refetch(); },
        });
      });
    }
    return normalized;
  }, [results, single, requests, prepared.errors, batch, batchPrepared.errors, batchResults]);
}
