import { useCallback, useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import {
  useCustom,
  useCustomMutation,
  useDataProvider,
  useInvalidate,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import {
  resourceOperationTarget,
  refineInvalidationParams,
  resourceInvalidationTargets,
  useActiveGraphQLSchemaName,
  useSchemaFieldMetadata,
  type DataResourceMetadata,
} from "@angee/resources";

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
  const facetsKey = stableJson(activeFacets);
  const dataProvider = useDataProvider();
  const operationDocuments = useOperationDocuments();
  const facetRequests = useMemo(() => {
    if (!canQuery || !resource) return [];
    const document = groupDocumentForResource(
      operationDocuments,
      resource.schemaName,
      resource.modelLabel,
    );
    return activeFacets.map((facet) => ({
      facet,
      request: groupByRequest(
        resourceOperationTarget(resource, "groups"),
        facet,
        { document },
      ),
    }));
  }, [activeFacets, canQuery, facetsKey, operationDocuments, resource]);
  const queries = useQueries({
    queries: facetRequests.map(({ facet, request }) => ({
      queryKey: [
        "angee",
        "facets",
        request.dataProviderName,
        request.root,
        facet.id,
        stableJson(facet),
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
  return {
    facets: Object.fromEntries(
      facetRequests.map(({ facet, request }, index) => [
        facet.id,
        extractFacet(queries[index]?.data, request.root, facet),
      ]),
    ),
    fetching: queries.some((query) => query.isFetching),
    error: (queries.find((query) => query.error)?.error ?? null) as HttpError | null,
    refetch: () => {
      queries.forEach((query) => {
        void query.refetch();
      });
    },
  };
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
