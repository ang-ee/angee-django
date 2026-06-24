import { useCallback, useMemo } from "react";
import {
  useCustom,
  useCustomMutation,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import type { DataResourceMetadata } from "@angee/sdk";

import {
  aggregateRequest,
  deletePreviewRequest,
  extractAggregate,
  extractDeletePreview,
  extractFacets,
  extractGroupBy,
  facetsRequest,
  groupByRequest,
  type AggregateBucket,
  type AggregateRequestOptions,
  type DeletePreview,
  type DeletePreviewVariables,
  type FacetRequestSpec,
  type GroupByRequestOptions,
  type GroupByResult,
  type ResourceFacetResult,
} from "./operations";

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

const EMPTY_FACETS: readonly FacetRequestSpec[] = [];
const INERT_FACET_RESOURCE: DataResourceMetadata = {
  schemaName: "default",
  modelLabel: "",
  appLabel: "",
  modelName: "",
  publicIdField: "id",
  roots: { groups: "__angeeFacetNoop" },
  typeNames: {},
  capabilities: [],
  filterFields: [],
  orderFields: [],
  aggregateFields: [],
  groupByFields: [],
  relationAxes: [],
};

export function useAngeeAggregate(
  resource: DataResourceMetadata,
  options: AggregateRequestOptions & { enabled?: boolean } = {},
): UseAngeeAggregateResult {
  const { enabled = true, ...query } = options;
  const queryKey = stableJson(query);
  const request = useMemo(
    () => aggregateRequest(resource, query),
    [resource, queryKey],
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
  const request = useMemo(
    () => groupByRequest(resource, query),
    [resource, queryKey],
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
  const requestResource = canQuery && resource ? resource : INERT_FACET_RESOURCE;
  const request = useMemo(
    () => facetsRequest(requestResource, { facets: activeFacets }),
    [requestResource, facetsKey],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request.dataProviderName,
    meta: request.meta,
    queryOptions: {
      enabled: canQuery,
    },
  });
  const data = run.query.data?.data ?? run.result.data;
  return {
    facets: extractFacets(data, activeFacets),
    fetching: run.query.isFetching,
    error: run.query.error,
    refetch: () => {
      void run.query.refetch();
    },
  };
}

export function useAngeeDeletePreview(
  resource: DataResourceMetadata,
): UseAngeeDeletePreviewResult {
  const root = resource.roots.deletePreview ?? "";
  const run = useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
  const mutate = useCallback(
    async (variables: DeletePreviewVariables) => {
      const request = deletePreviewRequest(resource, variables);
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: variables,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      return extractDeletePreview(response.data, request.root);
    },
    [resource, run.mutateAsync],
  );
  return {
    preview: root ? extractDeletePreview(run.mutation.data?.data, root) : null,
    fetching: run.mutation.isPending,
    error: run.mutation.error,
    mutate,
    reset: run.mutation.reset,
  };
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
