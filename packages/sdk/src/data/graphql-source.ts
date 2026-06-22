import { useMemo } from "react";

import { DISABLED_DOCUMENTS } from "../disabled-documents";
import {
  useModelMetadata,
  useModelRootFields,
  type DataQuerySurfaceMetadata,
  type ModelRootFieldMetadata,
} from "../model-metadata";
import {
  assembleAggregateDocument,
  assembleFacetsDocument,
  assembleGroupByDocument,
  assembleListDocument,
  clampPageSize,
} from "../selection";
import type { AggregateMeasure } from "../aggregate-extract";
import type { ResourceTypeName } from "../resource-types";
import {
  dataQueryGroupField,
  dataQueryGroupKey,
  dataQueryPage,
  type DataQuery,
  type DataQueryGroup,
  type DataQueryGroupOrder,
} from "./query";

type DataQueryFilterValue<TName extends ResourceTypeName> =
  DataQuery<TName>["filter"];
type DataQueryOrderValue<TName extends ResourceTypeName> =
  DataQuery<TName>["order"];

export interface GraphQLListDocumentQuery<
  TName extends ResourceTypeName = ResourceTypeName,
> {
  fields: readonly string[];
  filter?: DataQueryFilterValue<TName>;
  order?: DataQueryOrderValue<TName>;
}

export interface GraphQLListVariablesQuery<
  TName extends ResourceTypeName = ResourceTypeName,
> {
  filter?: DataQueryFilterValue<TName>;
  order?: DataQueryOrderValue<TName>;
  page: number;
  pageSize: number;
}

export interface GraphQLAggregateQuery<
  TName extends ResourceTypeName = ResourceTypeName,
> {
  filter?: DataQueryFilterValue<TName>;
  measures?: readonly AggregateMeasure[];
}

export interface GraphQLGroupByQuery<
  TName extends ResourceTypeName = ResourceTypeName,
> extends GraphQLAggregateQuery<TName> {
  groups: readonly DataQueryGroup[];
  groupOrder?: readonly DataQueryGroupOrder[];
  page?: number;
  pageSize?: number;
}

export interface GraphQLFacetQuery {
  id: string;
  groups: readonly DataQueryGroup[];
  filter?: DataQueryFilterValue<ResourceTypeName>;
  groupOrder?: readonly DataQueryGroupOrder[];
  page?: number;
  pageSize?: number;
  measures?: readonly AggregateMeasure[];
}

export interface GraphQLFacetsQuery<
  TName extends ResourceTypeName = ResourceTypeName,
> {
  filter?: DataQueryFilterValue<TName>;
  facets: readonly GraphQLFacetQuery[];
}

export interface GraphQLDataSource {
  modelLabel: string;
  dataQuery: DataQuerySurfaceMetadata | null;
  rootFields: ModelRootFieldMetadata | null;
  canQuery: boolean;
  listDocument: (query: GraphQLListDocumentQuery) => string;
  listVariables: (query: GraphQLListVariablesQuery) => Record<string, unknown>;
  aggregateDocument: (query?: GraphQLAggregateQuery) => string;
  aggregateVariables: (query?: GraphQLAggregateQuery) => Record<string, unknown>;
  groupByDocument: (
    query: GraphQLGroupByQuery,
    options?: { withFilterEcho?: boolean },
  ) => string;
  groupByVariables: (query: GraphQLGroupByQuery) => Record<string, unknown>;
  facetsDocument: (
    query: GraphQLFacetsQuery,
    options?: { withFilterEcho?: boolean },
  ) => string;
  facetsVariables: (query: GraphQLFacetsQuery) => Record<string, unknown>;
}

const NO_VARIABLES: Record<string, unknown> = {};

export function createGraphQLDataSource({
  modelLabel,
  rootFields,
  dataQuery = null,
}: {
  modelLabel: string;
  rootFields: ModelRootFieldMetadata | null;
  dataQuery?: DataQuerySurfaceMetadata | null;
}): GraphQLDataSource {
  return {
    modelLabel,
    dataQuery,
    rootFields,
    canQuery: rootFields !== null,
    listDocument(query) {
      if (!rootFields) return DISABLED_DOCUMENTS.query;
      return assembleListDocument(modelLabel, query.fields, rootFields, {
        withFilter: query.filter !== undefined,
        withOrder: query.order !== undefined,
      });
    },
    listVariables(query) {
      const limit = clampPageSize(query.pageSize);
      return {
        pagination: {
          offset: (dataQueryPage(query) - 1) * limit,
          limit,
        },
        ...(query.filter !== undefined ? { filters: query.filter } : {}),
        ...(query.order !== undefined ? { order: query.order } : {}),
      };
    },
    aggregateDocument(query = {}) {
      if (!rootFields) return DISABLED_DOCUMENTS.query;
      return assembleAggregateDocument(modelLabel, rootFields, {
        withFilter: query.filter !== undefined,
        measures: query.measures,
      });
    },
    aggregateVariables(query = {}) {
      return query.filter !== undefined ? { filter: query.filter } : NO_VARIABLES;
    },
    groupByDocument(query, options = {}) {
      if (!rootFields) return DISABLED_DOCUMENTS.query;
      return assembleGroupByDocument(modelLabel, rootFields, {
        keyFields: query.groups.map(dataQueryGroupKey),
        measures: query.measures,
        withFilter: query.filter !== undefined,
        withOrderBy: query.groupOrder !== undefined,
        withFilterEcho: options.withFilterEcho ?? false,
      });
    },
    groupByVariables(query) {
      return {
        groupBy: query.groups.map((group) => ({
          field: dataQueryGroupField(group),
          ...(group.granularity ? { granularity: group.granularity } : {}),
        })),
        pagination: groupPaginationVariables(query.page, query.pageSize) ?? null,
        ...(query.filter !== undefined ? { filter: query.filter } : {}),
        ...(query.groupOrder !== undefined ? { orderBy: query.groupOrder } : {}),
      };
    },
    facetsDocument(query, options = {}) {
      if (!rootFields || query.facets.length === 0) {
        return DISABLED_DOCUMENTS.query;
      }
      return assembleFacetsDocument(modelLabel, rootFields, {
        facets: query.facets.map((facet) => ({
          keyFields: facet.groups.map(dataQueryGroupKey),
          withFilter: (facet.filter ?? query.filter) !== undefined,
          measures: facet.measures,
          withOrderBy: facet.groupOrder !== undefined,
        })),
        withFilterEcho: options.withFilterEcho ?? false,
      });
    },
    facetsVariables(query) {
      const variables: Record<string, unknown> = {};
      query.facets.forEach((facet, index) => {
        variables[`groupBy${index}`] = facet.groups.map((group) => ({
          field: dataQueryGroupField(group),
          ...(group.granularity ? { granularity: group.granularity } : {}),
        }));
        variables[`pagination${index}`] =
          groupPaginationVariables(facet.page, facet.pageSize) ?? null;
        const filter = facet.filter ?? query.filter;
        if (filter !== undefined) {
          variables[`filter${index}`] = filter;
        }
        if (facet.groupOrder !== undefined) {
          variables[`orderBy${index}`] = facet.groupOrder;
        }
      });
      return variables;
    },
  };
}

/** Return the GraphQL data source for one model in the active schema. */
export function useGraphQLDataSource(modelLabel: string): GraphQLDataSource {
  const modelMetadata = useModelMetadata(modelLabel);
  const rootFields = useModelRootFields(modelLabel);
  const dataQuery = modelMetadata?.dataQuery ?? null;
  return useMemo(
    () => createGraphQLDataSource({ modelLabel, rootFields, dataQuery }),
    [dataQuery, modelLabel, rootFields],
  );
}

function groupPaginationVariables(
  page: number | undefined,
  pageSize: number | undefined,
): Record<string, number> | undefined {
  if (pageSize === undefined) return undefined;
  const limit = clampPageSize(pageSize);
  return { offset: (dataQueryPage({ page }) - 1) * limit, limit };
}
