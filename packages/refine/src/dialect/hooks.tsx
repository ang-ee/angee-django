import { useCallback, useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import {
  useCustom,
  useCustomMutation,
  useDataProvider,
  useInvalidate,
  useKeys,
  useResourceSubscription,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";

import {
  crudFiltersFromFilterRecord,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
} from "../filter-codec";
import {
  aggregateRequest,
  actionRequest,
  deletePreviewRequest,
  extractActionOutcome,
  extractAggregate,
  runActionResult,
  extractDeletePreview,
  extractFacet,
  extractGroupBy,
  extractRevisions,
  extractSaveResult,
  groupByRequest,
  revisionsRequest,
  saveRequest,
  type AggregateBucket,
  type AggregateRequestOptions,
  type ByIdVariables,
  type CustomGraphQLOperationTarget,
  type DeletePreview,
  type DeletePreviewVariables,
  type FacetRequestSpec,
  type GroupByRequestOptions,
  type GroupByResult,
  type ResourceRevision,
  type ResourceFacetResult,
  type ResourceSaveVariables,
} from "../operations";
import {
  operationDocument,
  useOperationDocuments,
} from "../operation-documents";
import { stableKey } from "../stable-deps";

type Row = Record<string, unknown>;
type InvalidateParams = Parameters<ReturnType<typeof useInvalidate>>[0];

export interface ListBatchTarget {
  dataProviderName: string | undefined;
  resourceIdentifier: string;
  resourceName: string;
}

export interface DialectDocumentOptions {
  document: unknown;
}

export interface UseAngeeAggregateResult {
  aggregate: AggregateBucket | null;
  fetching: boolean;
  error: HttpError | null;
  refetch: () => void;
}

export interface UseAngeeGroupByResult extends GroupByResult {
  fetching: boolean;
  error: HttpError | null;
  refetch: () => void;
}

export interface UseAngeeFacetsOptions {
  enabled?: boolean;
  facets: readonly FacetRequestSpec[];
}

/** One `_groups` request in a batch, addressed by a caller-stable `key`. */
export interface GroupByBatchScope {
  key: string;
  query: GroupByRequestOptions;
}

/** One leaf `list` request in a batch, addressed by a caller-stable `key`. */
export interface AngeeListBatchScope {
  key: string;
  filter: Record<string, unknown> | undefined;
  order: Record<string, unknown> | undefined;
  page: number;
  pageSize: number;
}

export interface AngeeListBatchEntry {
  rows: readonly Row[];
  total: number | undefined;
  fetching: boolean;
  error: Error | null;
}

interface GroupByRequestBatchEntry {
  data: unknown;
  fetching: boolean;
  error: HttpError | null;
  refetch: () => void;
}

export interface UseAngeeFacetsResult {
  facets: Readonly<Record<string, ResourceFacetResult>>;
  fetching: boolean;
  error: HttpError | null;
  refetch: () => void;
}

export interface UseAngeeDeletePreviewResult {
  preview: DeletePreview | null;
  fetching: boolean;
  error: HttpError | null;
  mutate: (variables: DeletePreviewVariables) => Promise<DeletePreview | null>;
  reset: () => void;
}

export interface UseAngeeRevisionsResult {
  revisions: readonly ResourceRevision[];
  count: number;
  fetching: boolean;
  error: HttpError | null;
  refetch: () => void;
}

export interface UseAngeeResourceSaveResult {
  save: (variables: ResourceSaveVariables) => Promise<Row | null>;
  fetching: boolean;
  error: HttpError | null;
  reset: () => void;
}

export type ActionMutate = (id: string) => Promise<string | undefined>;

export interface UseActionMutationOptions {
  dataProviderName?: string;
  /** Refine invalidation calls this action should trigger after success. */
  invalidates?: readonly InvalidateParams[];
}

export interface UseActionMutationState {
  fetching: boolean;
  error: Error | null;
}

const EMPTY_FACETS: readonly FacetRequestSpec[] = [];
const EMPTY_GROUP_BY_SCOPES: readonly GroupByBatchScope[] = [];
const EMPTY_LIST_SCOPES: readonly AngeeListBatchScope[] = [];

export function useAngeeAggregate(
  target: CustomGraphQLOperationTarget | null,
  options: AggregateRequestOptions & DialectDocumentOptions & { enabled?: boolean },
): UseAngeeAggregateResult {
  const { document, enabled = true, ...query } = options;
  const queryKey = stableKey(query);
  const canQuery = enabled && target !== null;
  const request = useMemo(
    () => (target ? aggregateRequest(target, query, { document }) : null),
    [document, target, queryKey],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request?.dataProviderName,
    meta: request?.meta,
    queryOptions: { enabled: canQuery },
  });
  const data = run.query.data?.data ?? run.result.data;
  return {
    aggregate: request ? extractAggregate(data, request.root) : null,
    fetching: run.query.isFetching,
    error: run.query.error,
    refetch: () => {
      void run.query.refetch();
    },
  };
}

export function useAngeeGroupBy(
  target: CustomGraphQLOperationTarget | null,
  options: GroupByRequestOptions & DialectDocumentOptions & { enabled?: boolean },
): UseAngeeGroupByResult {
  const { document, enabled = true, ...query } = options;
  const queryKey = stableKey(query);
  const canQuery = enabled && target !== null;
  const request = useMemo(
    () => (target ? groupByRequest(target, query, { document }) : null),
    [document, target, queryKey],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request?.dataProviderName,
    meta: request?.meta,
    queryOptions: { enabled: canQuery },
  });
  const data = run.query.data?.data ?? run.result.data;
  const result = request ? extractGroupBy(data, request.root) : { count: 0, buckets: [] };
  return {
    ...result,
    fetching: run.query.isFetching,
    error: run.query.error,
    refetch: () => {
      void run.query.refetch();
    },
  };
}

export function useAngeeFacets(
  target: CustomGraphQLOperationTarget | null,
  options: UseAngeeFacetsOptions & DialectDocumentOptions,
): UseAngeeFacetsResult {
  const { document, enabled = true, facets } = options;
  const canQuery = enabled && target !== null && facets.length > 0;
  const activeFacets = canQuery ? facets : EMPTY_FACETS;
  const scopes = useMemo<readonly GroupByBatchScope[]>(
    () => activeFacets.map((facet) => ({ key: facet.id, query: facet })),
    [activeFacets],
  );
  const batch = useGroupByRequestBatch(target, scopes, { document, enabled: canQuery });
  const root = target?.root ?? "";
  return useMemo(() => {
    const values = [...batch.values()];
    return {
      facets: Object.fromEntries(
        activeFacets.map((facet) => [
          facet.id,
          extractFacet(batch.get(facet.id)?.data, root, facet),
        ]),
      ),
      fetching: values.some((entry) => entry.fetching),
      error: values.find((entry) => entry.error)?.error ?? null,
      refetch: () => {
        values.forEach((entry) => entry.refetch());
      },
    };
  }, [activeFacets, batch, root]);
}

/**
 * Batch any number of `_groups` requests through one {@link useQueries} call: the
 * dynamic-length array is a single hook, so the request set can grow over renders
 * (e.g. a grouped surface revealing deeper sub-group levels as parents resolve)
 * without breaking the rules of hooks. The shared core behind {@link useAngeeFacets}
 * and {@link useAngeeGroupByBatch}; results are addressed by each scope's `key`.
 */
function useGroupByRequestBatch(
  target: CustomGraphQLOperationTarget | null,
  scopes: readonly GroupByBatchScope[],
  options: DialectDocumentOptions & { enabled?: boolean },
): ReadonlyMap<string, GroupByRequestBatchEntry> {
  const { document, enabled = true } = options;
  const canQuery = enabled && target !== null;
  const activeScopes = canQuery ? scopes : EMPTY_GROUP_BY_SCOPES;
  const scopesKey = stableKey(activeScopes);
  const dataProvider = useDataProvider();
  const requests = useMemo(() => {
    if (!canQuery || !target) return [];
    return activeScopes.map((scope) => ({
      key: scope.key,
      queryKey: stableKey(scope.query),
      request: groupByRequest(target, scope.query, { document }),
    }));
  }, [activeScopes, canQuery, document, target, scopesKey]);
  const queries = useQueries({
    queries: requests.map(({ key, queryKey, request }) => ({
      queryKey: [
        "angee",
        "group-by",
        request.dataProviderName,
        request.root,
        key,
        queryKey,
      ],
      queryFn: async () => {
        const custom = dataProvider(request.dataProviderName).custom;
        if (!custom) {
          throw new Error(
            `Data provider "${request.dataProviderName}" does not support ` +
              "custom GraphQL requests.",
          );
        }
        const response = await custom<BaseRecord>({
          url: "",
          method: "post",
          meta: request.meta,
        });
        return response.data;
      },
      enabled: canQuery,
    })),
  });
  return useMemo(
    () =>
      new Map(
        requests.map(({ key }, index) => {
          const query = queries[index];
          return [
            key,
            {
              data: query?.data,
              fetching: query?.isFetching ?? false,
              error: (query?.error ?? null) as HttpError | null,
              refetch: () => {
                void query?.refetch();
              },
            },
          ] as const;
        }),
      ),
    [requests, queries],
  );
}

/**
 * Batch the per-level `_groups` aggregates of a server-grouped view into one
 * request round. Each scope resolves to the same {@link GroupByResult} a single
 * {@link useAngeeGroupBy} would, keyed by the scope's `key`.
 */
export function useAngeeGroupByBatch(
  target: CustomGraphQLOperationTarget | null,
  scopes: readonly GroupByBatchScope[],
  options: DialectDocumentOptions & { enabled?: boolean },
): ReadonlyMap<string, UseAngeeGroupByResult> {
  const batch = useGroupByRequestBatch(target, scopes, options);
  const root = target?.root ?? "";
  return useMemo(
    () =>
      new Map(
        [...batch.entries()].map(([key, entry]) => [
          key,
          {
            ...extractGroupBy(entry.data, root),
            fetching: entry.fetching,
            error: entry.error,
            refetch: entry.refetch,
          },
        ]),
      ),
    [batch, root],
  );
}

/**
 * Batch the leaf record pages of a server-grouped view into one {@link useQueries}
 * round — one `getList` per currently-rendered expanded bucket, instead of one
 * `useList` hook mounted per bucket. The query keys mirror refine's `useList` so
 * `useInvalidate` reaches them, and a single static resource-level subscription
 * re-opens the live `changes()` feed (key parity alone never re-subscribes).
 */
export function useAngeeListBatch(
  target: ListBatchTarget | null,
  scopes: readonly AngeeListBatchScope[],
  options: { fields: readonly string[]; enabled?: boolean },
): ReadonlyMap<string, AngeeListBatchEntry> {
  const enabled = options.enabled ?? true;
  const canQuery = enabled && target !== null;
  const activeScopes = canQuery ? scopes : EMPTY_LIST_SCOPES;
  const { keys } = useKeys();
  const dataProvider = useDataProvider();
  const resourceName = target?.resourceName ?? "";
  const identifier = target?.resourceIdentifier ?? "";
  const schemaName = target?.dataProviderName;
  const fieldsKey = stableKey(options.fields);
  const listMeta = useMemo(
    () => ({ fields: refineFieldsFromPaths(options.fields) }),
    [fieldsKey],
  );
  const scopesKey = stableKey(activeScopes);
  const requests = useMemo(
    () =>
      activeScopes.map((scope) => ({
        scope,
        filters: crudFiltersFromFilterRecord(scope.filter) ?? [],
        sorters: refineSortersFromAngeeOrder(scope.order) ?? [],
        pagination: {
          mode: "server" as const,
          currentPage: scope.page,
          pageSize: scope.pageSize,
        },
      })),
    [activeScopes, scopesKey],
  );
  // One static, resource-level live subscription re-opens the websocket changes()
  // feed for these rows: refine's auto liveMode invalidates the resource list cache
  // on each change, and the keys below are the very keys useList builds, so the
  // invalidation reaches them. (Matching query keys alone never re-subscribes.)
  useResourceSubscription({
    channel: `resources/${resourceName}`,
    resource: resourceName,
    types: ["*"],
    params: { subscriptionType: "useList" },
    enabled: canQuery,
    meta: { dataProviderName: schemaName },
  });
  const queries = useQueries({
    queries: requests.map(({ filters, sorters, pagination }) => ({
      queryKey: keys()
        .data(schemaName)
        .resource(identifier)
        .action("list")
        .params({ ...listMeta, filters, pagination, sorters })
        .get(),
      queryFn: () =>
        dataProvider(schemaName).getList({
          resource: resourceName,
          pagination,
          filters,
          sorters,
          meta: listMeta,
        }),
      enabled: canQuery,
    })),
  });
  return useMemo(
    () =>
      new Map(
        requests.map(({ scope }, index) => {
          const query = queries[index];
          const data = query?.data;
          return [
            scope.key,
            {
              rows: (data?.data ?? []) as readonly Row[],
              total: data?.total,
              fetching: query?.isFetching ?? false,
              error: errorFromHttp(query?.error),
            },
          ] as const;
        }),
      ),
    [requests, queries],
  );
}

export function useAngeeDeletePreview(
  target: CustomGraphQLOperationTarget | null,
  options: DialectDocumentOptions,
): UseAngeeDeletePreviewResult {
  const { document } = options;
  const root = target?.root ?? "";
  const run = useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
  const mutate = useCallback(
    async (variables: DeletePreviewVariables) => {
      if (!target) return null;
      const request = deletePreviewRequest(target, variables, { document });
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: variables,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      return extractDeletePreview(response.data, request.root);
    },
    [document, target, run.mutateAsync],
  );
  return {
    preview: root ? extractDeletePreview(run.mutation.data?.data, root) : null,
    fetching: run.mutation.isPending,
    error: run.mutation.error,
    mutate,
    reset: run.mutation.reset,
  };
}

/**
 * Run the authored `<resource>_save(pk, patch, lines)` diff-apply mutation (F6)
 * through refine's custom mutation owner — the transactional parent-patch-plus-line
 * upsert/delete write. Mirrors `useAngeeDeletePreview`: the metadata edge resolves the
 * `save` root as `target`, and the caller passes the generated `document` for that
 * root (the single backend-binding point, see `saveRequest`). Returns the saved parent
 * row (with its returned lines) so the form re-seeds from the server truth.
 */
export function useAngeeResourceSave(
  target: CustomGraphQLOperationTarget | null,
  options: DialectDocumentOptions,
): UseAngeeResourceSaveResult {
  const { document } = options;
  const run = useCustomMutation<BaseRecord, HttpError, ResourceSaveVariables>();
  const save = useCallback(
    async (variables: ResourceSaveVariables) => {
      if (!target) return null;
      const request = saveRequest(target, variables, { document });
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: variables,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      return extractSaveResult(response.data, request.root);
    },
    [document, target, run.mutateAsync],
  );
  return {
    save,
    fetching: run.mutation.isPending,
    error: run.mutation.error,
    reset: run.mutation.reset,
  };
}

export function useAngeeRevisions(
  target: CustomGraphQLOperationTarget | null,
  id: string | null | undefined,
  options: DialectDocumentOptions & { enabled?: boolean },
): UseAngeeRevisionsResult {
  const { document, enabled = true } = options;
  const canQuery =
    enabled && target !== null && id !== null && id !== undefined && id !== "";
  const request = useMemo(
    () =>
      canQuery && target && id
        ? revisionsRequest(target, id, { document })
        : null,
    [canQuery, document, id, target],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request?.dataProviderName,
    meta: request?.meta,
    queryOptions: { enabled: canQuery },
  });
  const data = run.query.data?.data ?? run.result.data;
  const revisions = useMemo(
    () => (request ? extractRevisions(data, request.root) : []),
    [data, request],
  );
  return {
    revisions,
    count: revisions.length,
    fetching: run.query.isFetching,
    error: run.query.error,
    refetch: () => {
      void run.query.refetch();
    },
  };
}

/**
 * Run a single-id backend action mutation through refine's custom mutation
 * owner. The generated `ActionFieldName` union from `@angee/gql/<schema>/actions`
 * still pins callers to real action fields, while refine owns execution state.
 */
export function useActionMutation<TField extends string = string>(
  field: TField,
  options: UseActionMutationOptions = {},
): [ActionMutate, UseActionMutationState] {
  const dataProviderName = options.dataProviderName ?? "default";
  const operationDocuments = useOperationDocuments();
  const invalidate = useInvalidate();
  const invalidates = options.invalidates ?? EMPTY_INVALIDATIONS;
  const run = useCustomMutation<BaseRecord, HttpError, ByIdVariables>();
  const mutate = useCallback<ActionMutate>(
    async (id) => {
      const request = actionRequest(field, { id }, {
        dataProviderName,
        document: operationDocument(
          operationDocuments,
          dataProviderName,
          "actions",
          field,
        ),
      });
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: { id },
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      const result = runActionResult(extractActionOutcome(response.data, request.root));
      await Promise.all(
        invalidates.map((target) => invalidate(target)),
      );
      return result;
    },
    [
      dataProviderName,
      field,
      invalidate,
      invalidates,
      operationDocuments,
      run.mutateAsync,
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

const EMPTY_INVALIDATIONS: readonly InvalidateParams[] = [];

function errorFromHttp(error: unknown): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  const message =
    typeof error === "object" && error !== null && "message" in error
      ? String((error as { message?: unknown }).message)
      : String(error);
  return new Error(message);
}
