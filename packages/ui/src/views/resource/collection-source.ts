import * as React from "react";
import {
  useCanonicalResourceModelLabels,
  type ResourceQuery,
  type Row,
} from "@angee/metadata";
import {
  useAuthoredQueryBatch,
  useDerivedBatch,
  useReportRefusedReads,
  type AuthoredDocument,
  type AuthoredVariables,
  type DocumentData,
  type GroupByResult,
  type TypedDocumentNode,
} from "@angee/refine";
import { errorFromUnknown } from "../../data/errors";
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
  /**
   * Present on the leaf group level: each returned group must carry its first
   * page of this many rows in `bucket.rows`; later or resized pages use `rows`.
   */
  leafPageSize?: number;
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
  /** One page of rows: the flat list, or a later or resized page of one group. */
  rows: AnyCollectionQuery<CollectionPageRequest, CollectionPage<TRow>>;
  /** Group headers; at the leaf level each bucket carries its first page in `rows`. */
  groups?: AnyCollectionQuery<
    CollectionGroupRequest,
    GroupByResult & { summary?: string }
  >;
  /** The declared size of each group's first page (the groups read carries it). */
  leafPageSize?: number;
}

interface CollectionQueryResult<TResult> {
  data: TResult | undefined;
  fetching: boolean;
  error: Error | null;
  /** When the read last settled, as native reads state it; null until it has. */
  updatedAt: number | null;
  refetch: () => void;
}

/** Use the authored-query owner for a dynamic frontier of group or row requests. */
export function useCollectionQueryBatch<TRequest, TResult>(
  source: AnyCollectionQuery<TRequest, TResult> | undefined,
  requests: readonly (TRequest & { key: string })[],
): ReadonlyMap<string, CollectionQueryResult<TResult>> {
  const models = useCanonicalResourceModelLabels(source?.models);
  const prepared = React.useMemo(() => {
    const errors = new Map<string, Error | null>();
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
            errors.set(request.key, errorFromUnknown(error));
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
  // A result selects again only when its own read changes. A payload the source's
  // `select` refuses is that read's error, reported as a failed read is, never a
  // render that throws.
  const selected = useDerivedBatch(results, (result): CollectionQueryResult<TResult> => {
    let data: TResult | undefined;
    let refused: Error | null = null;
    if (result.data !== undefined && source) {
      try {
        data = source.select(result.data);
      } catch (cause) {
        refused = errorFromUnknown(cause);
      }
    }
    return {
      data,
      fetching: result.isFetching,
      error: result.error ?? refused,
      updatedAt: result.dataUpdatedAt || null,
      refetch: () => {
        void result.refetch();
      },
    };
  }, [source]);
  useReportRefusedReads(results, selected);
  const failed = React.useMemo(
    () => new Map<string, CollectionQueryResult<TResult>>([...prepared.errors].map(([key, error]) => [key, {
      data: undefined,
      fetching: false,
      error,
      updatedAt: null,
      refetch: () => {},
    }])),
    [prepared.errors],
  );
  return React.useMemo(
    () => failed.size === 0 ? selected : new Map([...selected, ...failed]),
    [selected, failed],
  );
}
