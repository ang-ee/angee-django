import * as React from "react";
import {
  useCanonicalResourceModelLabels,
  type ResourceQuery,
  type Row,
} from "@angee/metadata";
import {
  useAuthoredQueryBatch,
  type AuthoredDocument,
  type AuthoredVariables,
  type DocumentData,
  type GroupByResult,
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
export interface CollectionQuery<
  TDocument extends AuthoredDocument,
  TRequest,
  TResult,
> {
  document: TDocument;
  variables: (request: TRequest) => AuthoredVariables<TDocument>;
  select: (data: DocumentData<TDocument>) => TResult;
  models?: readonly string[];
  dataProviderName?: string;
  enabled?: boolean;
}

export type AnyCollectionQuery<TRequest, TResult> = CollectionQuery<
  TypedDocumentNode<unknown, Record<string, unknown>>,
  TRequest,
  TResult
>;

/** Keep document variables/results checked at the declaration, erase once here. */
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
export function useCollectionQueryBatch<TRequest, TResult>(
  source: AnyCollectionQuery<TRequest, TResult> | undefined,
  requests: readonly (TRequest & { key: string })[],
): ReadonlyMap<string, CollectionQueryResult<TResult>> {
  const models = useCanonicalResourceModelLabels(source?.models);
  const prepared = React.useMemo(() => {
    const errors = new Map<string, Error>();
    const scopes = source
      ? requests.flatMap((request) => {
          try {
            return [
              {
                key: request.key,
                document: source.document,
                variables: source.variables(request),
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
  }, [source, requests, models]);
  const results = useAuthoredQueryBatch(prepared.scopes, {
    dataProviderName: source?.dataProviderName,
    enabled: source?.enabled,
  });
  return React.useMemo(() => {
    const normalized = new Map<string, CollectionQueryResult<TResult>>(
      [...results].map(([key, result]) => [
        key,
        {
          data:
            result.data === undefined ? undefined : source?.select(result.data),
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
    return normalized;
  }, [results, source, prepared.errors]);
}
