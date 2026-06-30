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
  resourceOperationTarget,
  refineInvalidationParams,
  refineResourceIdentifier,
  refineResourceName,
  resourceInvalidationTargets,
  useActiveGraphQLSchemaName,
  useSchemaFieldMetadata,
  type DataResourceMetadata,
  type Row,
} from "@angee/resources";

import {
  aggregateRequest,
  actionRequest,
  crudFiltersFromFilterRecord,
  deletePreviewRequest,
  extractActionOutcome,
  extractAggregate,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
  runActionResult,
  extractDeletePreview,
  extractFacet,
  extractGroupBy,
  groupByRequest,
  type AggregateBucket,
  type AggregateRequestOptions,
  type ByIdVariables,
  type DeletePreview,
  type DeletePreviewVariables,
  type FacetRequestSpec,
  type GroupByRequestOptions,
  type GroupByResult,
  type ResourceFacetResult,
} from "@angee/refine";
import {
  aggregateDocumentForResource,
  actionDocumentForSchema,
  deletePreviewDocumentForResource,
  groupDocumentForResource,
  useOperationDocuments,
} from "@angee/refine";

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

export type ActionMutate = (id: string) => Promise<string | undefined>;

export interface UseActionMutationOptions {
  dataProviderName?: string;
  /** Extra Angee model labels whose refine caches this action mutates. */
  invalidateModels?: readonly string[];
}

export interface UseActionMutationState {
  fetching: boolean;
  error: Error | null;
}

const EMPTY_FACETS: readonly FacetRequestSpec[] = [];
const EMPTY_GROUP_BY_SCOPES: readonly GroupByBatchScope[] = [];
const EMPTY_LIST_SCOPES: readonly AngeeListBatchScope[] = [];

export function useAngeeAggregate(
  resource: DataResourceMetadata,
  options: AggregateRequestOptions & { enabled?: boolean } = {},
): UseAngeeAggregateResult {
  const { enabled = true, ...query } = options;
  const queryKey = stableJson(query);
  const operationDocuments = useOperationDocuments();
  const request = useMemo(
    () => aggregateRequest(resourceOperationTarget(resource, "aggregate"), query, {
      document: aggregateDocumentForResource(
        operationDocuments,
        resource.schemaName,
        resource.modelLabel,
      ),
    }),
    [operationDocuments, resource, queryKey],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request.dataProviderName,
    meta: request.meta,
    queryOptions: { enabled },
  });
  const data = run.query.data?.data ?? run.result.data;
  return {
    aggregate: extractAggregate(data, request.root),
    fetching: run.query.isFetching,
    error: run.query.error,
    refetch: () => {
      void run.query.refetch();
    },
  };
}

export function useAngeeGroupBy(
  resource: DataResourceMetadata,
  options: GroupByRequestOptions & { enabled?: boolean },
): UseAngeeGroupByResult {
  const { enabled = true, ...query } = options;
  const queryKey = stableJson(query);
  const operationDocuments = useOperationDocuments();
  const request = useMemo(
    () => groupByRequest(resourceOperationTarget(resource, "groups"), query, {
      document: groupDocumentForResource(
        operationDocuments,
        resource.schemaName,
        resource.modelLabel,
      ),
    }),
    [operationDocuments, resource, queryKey],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request.dataProviderName,
    meta: request.meta,
    queryOptions: { enabled },
  });
  const data = run.query.data?.data ?? run.result.data;
  const result = extractGroupBy(data, request.root);
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
  resource: DataResourceMetadata | null,
  options: UseAngeeFacetsOptions,
): UseAngeeFacetsResult {
  const { enabled = true, facets } = options;
  const canQuery = enabled && Boolean(resource?.roots.groups) && facets.length > 0;
  const activeFacets = canQuery ? facets : EMPTY_FACETS;
  const scopes = useMemo<readonly GroupByBatchScope[]>(
    () => activeFacets.map((facet) => ({ key: facet.id, query: facet })),
    [activeFacets],
  );
  const batch = useGroupByRequestBatch(resource, scopes, { enabled: canQuery });
  const root = resource?.roots.groups ?? "";
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
  resource: DataResourceMetadata | null,
  scopes: readonly GroupByBatchScope[],
  options: { enabled?: boolean } = {},
): ReadonlyMap<string, GroupByRequestBatchEntry> {
  const enabled = options.enabled ?? true;
  const canQuery = enabled && Boolean(resource?.roots.groups);
  const activeScopes = canQuery ? scopes : EMPTY_GROUP_BY_SCOPES;
  const scopesKey = stableJson(activeScopes);
  const dataProvider = useDataProvider();
  const operationDocuments = useOperationDocuments();
  const requests = useMemo(() => {
    if (!canQuery || !resource) return [];
    const document = groupDocumentForResource(
      operationDocuments,
      resource.schemaName,
      resource.modelLabel,
    );
    const target = resourceOperationTarget(resource, "groups");
    return activeScopes.map((scope) => ({
      key: scope.key,
      queryKey: stableJson(scope.query),
      request: groupByRequest(target, scope.query, { document }),
    }));
  }, [activeScopes, canQuery, operationDocuments, resource, scopesKey]);
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
  resource: DataResourceMetadata | null,
  scopes: readonly GroupByBatchScope[],
  options: { enabled?: boolean } = {},
): ReadonlyMap<string, UseAngeeGroupByResult> {
  const batch = useGroupByRequestBatch(resource, scopes, options);
  const root = resource?.roots.groups ?? "";
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
  resource: DataResourceMetadata | null,
  scopes: readonly AngeeListBatchScope[],
  options: { fields: readonly string[]; enabled?: boolean },
): ReadonlyMap<string, AngeeListBatchEntry> {
  const enabled = options.enabled ?? true;
  const canQuery = enabled && Boolean(resource?.roots.list);
  const activeScopes = canQuery ? scopes : EMPTY_LIST_SCOPES;
  const { keys } = useKeys();
  const dataProvider = useDataProvider();
  const resourceName = resource ? refineResourceName(resource) : "";
  const identifier = resource ? refineResourceIdentifier(resource) : "";
  const schemaName = resource?.schemaName;
  const fieldsKey = stableJson(options.fields);
  const listMeta = useMemo(
    () => ({ fields: refineFieldsFromPaths(options.fields) }),
    [fieldsKey],
  );
  const scopesKey = stableJson(activeScopes);
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
  resource: DataResourceMetadata,
): UseAngeeDeletePreviewResult {
  const root = resource.roots.deletePreview ?? "";
  const run = useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
  const operationDocuments = useOperationDocuments();
  const mutate = useCallback(
    async (variables: DeletePreviewVariables) => {
      const request = deletePreviewRequest(
        resourceOperationTarget(resource, "deletePreview"),
        variables,
        {
          document: deletePreviewDocumentForResource(
            operationDocuments,
            resource.schemaName,
            resource.modelLabel,
          ),
        },
      );
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: variables,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      return extractDeletePreview(response.data, request.root);
    },
    [operationDocuments, resource, run.mutateAsync],
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
 * Run a single-id backend action mutation through refine's custom mutation
 * owner. The generated `ActionFieldName` union from `@angee/gql/<schema>/actions`
 * still pins callers to real action fields, while refine owns execution state.
 */
export function useActionMutation<TField extends string = string>(
  field: TField,
  options: UseActionMutationOptions = {},
): [ActionMutate, UseActionMutationState] {
  const activeSchema = useActiveGraphQLSchemaName();
  const dataProviderName = options.dataProviderName ?? activeSchema ?? "default";
  const metadata = useSchemaFieldMetadata();
  const operationDocuments = useOperationDocuments();
  const invalidate = useInvalidate();
  const invalidateModels = options.invalidateModels ?? EMPTY_MODEL_LABELS;
  const invalidationTargets = useMemo(
    () => resourceInvalidationTargets(metadata, invalidateModels),
    [metadata, invalidateModels],
  );
  const run = useCustomMutation<BaseRecord, HttpError, ByIdVariables>();
  const mutate = useCallback<ActionMutate>(
    async (id) => {
      const request = actionRequest(field, { id }, {
        dataProviderName,
        document: actionDocumentForSchema(
          operationDocuments,
          dataProviderName,
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
        invalidationTargets.map((target) =>
          invalidate(refineInvalidationParams(target)),
        ),
      );
      return result;
    },
    [
      dataProviderName,
      field,
      invalidate,
      invalidationTargets,
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

const EMPTY_MODEL_LABELS: readonly string[] = [];

function errorFromHttp(error: unknown): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  const message =
    typeof error === "object" && error !== null && "message" in error
      ? String((error as { message?: unknown }).message)
      : String(error);
  return new Error(message);
}

function stableJson(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, sortJson(item)]),
  );
}
