import { useMemo } from "react";

import {
  autoExtractGroupBy,
  type AggregateMeasure,
  type GroupByResult,
} from "../aggregate-extract";
import { useOptionalDocumentQuery } from "../document-query";
import { useRegisterModelRefetch } from "../relay-invalidation";
import type { ResourceTypeName } from "../resource-types";
import { useStableValue, useStableVariables } from "../stable-deps";
import {
  useGraphQLDataSource,
  type GraphQLFacetQuery,
} from "./graphql-source";
import {
  dataQueryGroupKey,
  type DataQueryFilter,
  type DataQueryGroup,
  type DataQueryGroupOrder,
} from "./query";
import {
  Filter,
  type DataViewFilter,
} from "./view-state";

const EMPTY_FACETS: readonly ResourceFacetSpec[] = [];

export interface ResourceFacetSpec {
  /** Stable caller-owned identifier for this facet result. */
  id: string;
  /** Group dimensions backing this facet. */
  groups: readonly DataQueryGroup[];
  /** Bucket-key field used as the option value; defaults to the first group key. */
  valueKey?: string;
  /** Bucket-key field used as the option label; defaults to `valueKey`. */
  labelKey?: string;
  /** Server-side ordering for this facet's buckets. */
  groupOrder?: readonly DataQueryGroupOrder[];
  /** Filter fields to remove from this facet's count query. */
  neutralizeFilterFields?: readonly string[];
  page?: number;
  pageSize?: number;
  measures?: readonly AggregateMeasure[];
}

export interface ResourceFacetOption {
  value: string;
  label: string;
  count: number;
  key: Record<string, unknown>;
  /** Backend-echoed filter for this bucket; preferred over frontend synthesis. */
  filter?: Record<string, unknown>;
}

export interface ResourceFacetResult {
  count: number;
  totalCount: number;
  options: readonly ResourceFacetOption[];
}

export interface UseResourceFacetsOptions<
  TName extends ResourceTypeName = ResourceTypeName,
> {
  enabled?: boolean;
  filter?: DataQueryFilter<TName>;
  facets: readonly ResourceFacetSpec[];
  pageSize?: number;
  withFilterEcho?: boolean;
}

/** Fetch grouped bucket facets for one model in a single GraphQL operation. */
export function useResourceFacets<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: string,
  options: UseResourceFacetsOptions<TName>,
): {
  facets: Readonly<Record<string, ResourceFacetResult>>;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
} {
  const {
    enabled = true,
    facets,
    filter,
    pageSize,
    withFilterEcho = true,
  } = options;
  const withFilter = filter !== undefined;
  const source = useGraphQLDataSource(modelLabel);
  const stableFacets = useStableValue<readonly ResourceFacetSpec[]>(
    facets.map((facet) => ({
      ...facet,
      ...(facet.pageSize === undefined && pageSize !== undefined
        ? { pageSize }
        : {}),
    })),
    EMPTY_FACETS,
  );
  const graphQLFacets = useMemo<readonly GraphQLFacetQuery[]>(
    () =>
      stableFacets.map((facet) => ({
        id: facet.id,
        groups: facet.groups,
        ...(withFilter
          ? { filter: facetFilter(filter, facet.neutralizeFilterFields) }
          : {}),
        ...(facet.groupOrder !== undefined
          ? { groupOrder: facet.groupOrder }
          : {}),
        ...(facet.page !== undefined ? { page: facet.page } : {}),
        ...(facet.pageSize !== undefined ? { pageSize: facet.pageSize } : {}),
        ...(facet.measures !== undefined ? { measures: facet.measures } : {}),
      })),
    [filter, stableFacets, withFilter],
  );
  const active =
    enabled && Boolean(modelLabel) && graphQLFacets.length > 0 && source.canQuery;
  const variables = useStableVariables(
    source.facetsVariables({
      facets: graphQLFacets,
    }),
  );
  const document = useMemo(
    () =>
      source.facetsDocument(
        {
          facets: graphQLFacets,
        },
        { withFilterEcho },
      ),
    [source, graphQLFacets, withFilterEcho],
  );
  const run = useOptionalDocumentQuery(document, variables, active);
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  const facetResults = useMemo(
    () => extractResourceFacetResults(run.data, stableFacets),
    [run.data, stableFacets],
  );
  return {
    facets: facetResults,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

/** Convert aliased grouped aggregate data (`facet0`, `facet1`, …) to options. */
export function extractResourceFacetResults(
  data: unknown,
  facets: readonly ResourceFacetSpec[],
): Readonly<Record<string, ResourceFacetResult>> {
  return Object.fromEntries(
    facets.map((facet, index) => [
      facet.id,
      resourceFacetResult(autoExtractGroupBy(data, `facet${index}`), facet),
    ]),
  );
}

function resourceFacetResult(
  result: GroupByResult,
  facet: ResourceFacetSpec,
): ResourceFacetResult {
  return {
    count: result.count,
    totalCount: result.totalCount,
    options: result.buckets.flatMap((bucket) => {
      const key = bucket.key ?? {};
      const valueKey = facet.valueKey ?? firstGroupKey(facet);
      const value = valueKey ? stringValue(key[valueKey]) : null;
      if (value === null) return [];
      const labelKey = facet.labelKey ?? valueKey;
      const label = labelKey ? stringValue(key[labelKey]) ?? value : value;
      return [{
        value,
        label,
        count: bucket.count,
        key,
        ...(bucket.filter ? { filter: bucket.filter } : {}),
      }];
    }),
  };
}

function firstGroupKey(facet: ResourceFacetSpec): string | undefined {
  const [group] = facet.groups;
  return group ? dataQueryGroupKey(group) : undefined;
}

function facetFilter<TName extends ResourceTypeName>(
  filter: DataQueryFilter<TName> | undefined,
  fields: readonly string[] | undefined,
): DataViewFilter {
  if (filter === undefined) return {};
  if (!fields || fields.length === 0) return filter as DataViewFilter;
  return Filter.from(filter).withoutFields(fields);
}

function stringValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") return String(value);
  const text = value.trim();
  return text === "" ? null : text;
}
