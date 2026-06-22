import * as React from "react";
import {
  useModelMetadata,
  useGraphQLProviderAvailable,
  useResourceFacets,
  useSchemaFieldMetadata,
  type ModelMetadata,
  type ModelRelationFilterMetadata,
  type ModelRelationFilterMode,
  type ResourceFacetSpec,
  type Row,
} from "@angee/sdk";

import type {
  DataToolbarFilterField,
  DataToolbarFilterOption,
  DataToolbarGroupOption,
} from "../toolbars";
import type { DataViewFilter, DataViewGroup } from "./data-view-model";
import {
  dataViewGroupToAggregateDimension,
  groupLabelDimension,
  groupLabelOrderField,
} from "./ListInternals";
import {
  fieldLabel,
  relationFieldInfo,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import type { ColumnDescriptor } from "./page";
import { useRelationOptions } from "./relation-options";

const RELATION_FACET_OPTION_LIMIT = 200;
const EMPTY_FILTER_OPTIONS: readonly DataToolbarFilterOption[] = [];
const EMPTY_FILTER_FIELDS: readonly DataToolbarFilterField[] = [];
const EMPTY_FACET_SPECS: readonly ResourceFacetSpec[] = [];
const EMPTY_RELATION_FACETS: Pick<RelationFacet, "filters" | "filterFields"> = {
  filters: EMPTY_FILTER_OPTIONS,
  filterFields: EMPTY_FILTER_FIELDS,
};

export interface RelationFacetOptions {
  /** Relation field on the current model, e.g. `provider`. */
  field: string;
  /** Toolbar label; defaults to the relation field name. */
  label?: React.ReactNode;
  /** Filter field accepted by the current model filter input; defaults to SDL metadata. */
  filterField?: string;
  /** Filter value shape; defaults to SDL metadata, then lookup for explicit fields. */
  filterMode?: ModelRelationFilterMode;
  /** Aggregate bucket key returned by the API; defaults to SDL metadata. */
  aggregateKey?: string;
  /** Related-record display field; defaults to the related model representation. */
  labelField?: string;
  /** Related rows fetched for the facet picker. */
  pageSize?: number;
  /** Custom group axis; `false` suppresses group option generation. */
  group?: DataViewGroup | false;
}

export interface RelationFacet {
  filters: readonly DataToolbarFilterOption[];
  filterFields: readonly DataToolbarFilterField[];
  groupOption?: DataToolbarGroupOption;
}

interface ColumnRelationFacet {
  id: string;
  label: React.ReactNode;
  filter: ModelRelationFilterMetadata;
  spec: ResourceFacetSpec;
}

/** Build toolbar filters/groups for a to-one relation using schema metadata. */
export function useRelationFacet(
  model: string,
  options: RelationFacetOptions,
): RelationFacet {
  const {
    aggregateKey: optionAggregateKey,
    field,
    filterField: optionFilterField,
    filterMode: optionFilterMode,
    group,
    label: optionLabel,
    labelField: optionLabelField,
    pageSize = RELATION_FACET_OPTION_LIMIT,
  } = options;
  const schemaMetadata = useSchemaFieldMetadata();
  const modelMetadata = useModelMetadata(model);
  const relation = React.useMemo(
    () => relationFieldInfo(field, modelMetadata, schemaMetadata),
    [field, modelMetadata, schemaMetadata],
  );
  const filter = React.useMemo(
    () =>
      relationFilterConfig(relation?.filter, {
        field: optionFilterField,
        mode: optionFilterMode,
      }),
    [optionFilterField, optionFilterMode, relation],
  );
  const aggregateKey = optionAggregateKey ?? filter?.aggregateKey;
  const label = optionLabel ?? relationLabel(field);
  const groupOption = React.useMemo(
    () =>
      relationGroupOption({
        aggregateKey,
        field,
        group,
        labelField: optionLabelField,
        relation,
        label,
      }),
    [aggregateKey, field, group, label, optionLabelField, relation],
  );
  const facetSpecs = React.useMemo(
    () =>
      relationFacetSpecs(groupOption?.group, modelMetadata, {
        id: filter?.field,
        pageSize,
      }),
    [filter?.field, groupOption?.group, modelMetadata, pageSize],
  );
  const facetQuery = useResourceFacets(model, {
    facets: facetSpecs,
    enabled: facetSpecs.length > 0,
  });
  const facetOptions = React.useMemo(
    () => (filter ? facetQuery.facets[filter.field]?.options ?? [] : []),
    [facetQuery.facets, filter],
  );
  const labelField = optionLabelField ?? relation?.labelField ?? "id";
  const { options: choiceOptions } = useRelationOptions(relation, {
    labelField,
    pageSize,
    enabled: facetSpecs.length === 0,
    sort: true,
  });
  const relationOptions = React.useMemo(
    () =>
      facetSpecs.length > 0
        ? facetOptions.map((option) => ({
            value: option.value,
            label: option.label,
          }))
        : choiceOptions,
    [choiceOptions, facetOptions, facetSpecs.length],
  );
  const filters = React.useMemo<readonly DataToolbarFilterOption[]>(
    () =>
      relation && filter
        ? (facetSpecs.length > 0 ? facetOptions : choiceOptions).map((option) => {
            const echoedFilter = "filter" in option ? option.filter : undefined;
            return {
              id: `${filter.field}:${option.value}`,
              label: option.label,
              chipLabel: option.label,
              filter: echoedFilter
                ? (echoedFilter as DataViewFilter)
                : relationFacetFilter(filter, option.value),
            };
          })
        : EMPTY_FILTER_OPTIONS,
    [choiceOptions, facetOptions, facetSpecs.length, filter, relation],
  );
  const filterFields = React.useMemo<readonly DataToolbarFilterField[]>(
    () =>
      relation && filter?.mode === "lookup" && isToolbarLookup(filter.lookup)
        ? [{
            id: filter.field,
            field: filter.field,
            label,
            type: "selection",
            options: relationOptions,
          }]
        : EMPTY_FILTER_FIELDS,
    [filter, label, relation, relationOptions],
  );

  return React.useMemo(
    () => ({
      filters,
      filterFields,
      ...(groupOption ? { groupOption } : {}),
    }),
    [filterFields, filters, groupOption],
  );
}

/** Build relation filter facets for visible relation columns in one model list. */
export function useRelationFacetsForColumns<TRow extends Row>(
  model: string,
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
): Pick<RelationFacet, "filters" | "filterFields"> {
  const facets = React.useMemo(
    () => relationFacetsForColumns(columns, metadata),
    [columns, metadata],
  );
  const canQueryFacets = useGraphQLProviderAvailable();
  const facetQuery = useResourceFacets(model, {
    facets: facets.map((facet) => facet.spec),
    enabled: canQueryFacets && facets.length > 0,
  });
  const filters = React.useMemo<readonly DataToolbarFilterOption[]>(
    () =>
      facets.flatMap((facet) => {
        const result = facetQuery.facets[facet.id];
        return (result?.options ?? []).map((option) => ({
          id: `${facet.filter.field}:${option.value}`,
          label: option.label,
          chipLabel: option.label,
          filter: option.filter
            ? (option.filter as DataViewFilter)
            : relationFacetFilter(facet.filter, option.value),
        }));
      }),
    [facetQuery.facets, facets],
  );
  const filterFields = React.useMemo<readonly DataToolbarFilterField[]>(
    () =>
      facets.flatMap((facet) => {
        const result = facetQuery.facets[facet.id];
        if (
          facet.filter.mode !== "lookup"
          || !isToolbarLookup(facet.filter.lookup)
        ) {
          return [];
        }
        return [{
          id: facet.filter.field,
          field: facet.filter.field,
          label: facet.label,
          type: "selection",
          options: (result?.options ?? []).map((option) => ({
            value: option.value,
            label: option.label,
          })),
        }];
      }),
    [facetQuery.facets, facets],
  );
  return React.useMemo(
    () =>
      canQueryFacets && facets.length > 0
        ? { filters, filterFields }
        : EMPTY_RELATION_FACETS,
    [canQueryFacets, facets.length, filterFields, filters],
  );
}

function isToolbarLookup(lookup: string | undefined): boolean {
  return lookup === undefined || lookup === "exact" || lookup === "inList";
}

function relationFilterConfig(
  metadata: ModelRelationFilterMetadata | undefined,
  override: {
    field: string | undefined;
    mode: ModelRelationFilterMode | undefined;
  },
): ModelRelationFilterMetadata | undefined {
  if (!override.field) return metadata;
  const sameField = override.field === metadata?.field;
  return {
    field: override.field,
    mode: override.mode ?? metadata?.mode ?? "lookup",
    lookup: sameField ? metadata?.lookup : "exact",
    ...(metadata?.aggregateKey ? { aggregateKey: metadata.aggregateKey } : {}),
  };
}

function relationFacetFilter(
  filter: ModelRelationFilterMetadata,
  value: string,
): DataViewFilter {
  if (filter.mode === "id") return { [filter.field]: value };
  const lookup = filter.lookup ?? "exact";
  return {
    [filter.field]: {
      [lookup]: lookup === "inList" ? [value] : value,
    },
  };
}

function relationFacetSpecs(
  group: DataViewGroup | undefined,
  metadata: ModelMetadata | null,
  options: {
    id: string | undefined;
    pageSize: number;
  },
): readonly ResourceFacetSpec[] {
  if (!group || !options.id) return EMPTY_FACET_SPECS;
  const identity = dataViewGroupToAggregateDimension(group);
  const label = groupLabelDimension(group, metadata);
  const labelOrderField = groupLabelOrderField(group, metadata);
  return [{
    id: options.id,
    groups: label ? [identity, label] : [identity],
    ...(identity.key ? { valueKey: identity.key } : {}),
    ...(label?.key ? { labelKey: label.key } : {}),
    ...(labelOrderField
      ? { groupOrder: [{ field: labelOrderField, direction: "ASC" as const }] }
      : {}),
    pageSize: options.pageSize,
  }];
}

function relationFacetsForColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
): readonly ColumnRelationFacet[] {
  if (!metadata) return [];
  const facets: ColumnRelationFacet[] = [];
  const seen = new Set<string>();
  for (const column of columns) {
    const facet = relationFacetForColumn(column, metadata);
    if (!facet || seen.has(facet.id)) continue;
    seen.add(facet.id);
    facets.push(facet);
  }
  return facets;
}

function relationFacetForColumn<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  metadata: ModelMetadata,
): ColumnRelationFacet | null {
  const [relationField, labelField, ...rest] = column.field.split(".");
  if (!relationField || !labelField || rest.length > 0) return null;
  const field = metadata.fields[relationField];
  const filter = field?.relationFilter;
  if (field?.kind !== "relation" || !filter?.aggregateKey) return null;
  if (!filterAllowed(filter, metadata)) return null;
  const group = {
    field: column.field,
    aggregateField: relationField,
    aggregateKey: filter.aggregateKey,
  };
  if (!groupAllowed(group, metadata)) return null;
  const [spec] = relationFacetSpecs(group, metadata, {
    id: filter.field,
    pageSize: RELATION_FACET_OPTION_LIMIT,
  });
  if (!spec) return null;
  return {
    id: filter.field,
    label: fieldLabel(relationField, field, column.header),
    filter,
    spec,
  };
}

function filterAllowed(
  filter: ModelRelationFilterMetadata,
  metadata: ModelMetadata,
): boolean {
  const filterFields = metadata.dataQuery?.filterFields;
  return !filterFields || filterFields.includes(filter.field);
}

function groupAllowed(group: DataViewGroup, metadata: ModelMetadata): boolean {
  const groupByFields = metadata.dataQuery?.groupByFields;
  if (!groupByFields) return true;
  const aggregateField = group.aggregateField ?? group.field;
  return groupByFields.includes(aggregateField) || groupByFields.includes(group.field);
}

function relationGroupOption({
  aggregateKey,
  field,
  group,
  label,
  labelField,
  relation,
}: {
  aggregateKey: string | undefined;
  field: string;
  group: DataViewGroup | false | undefined;
  label: React.ReactNode;
  labelField: string | undefined;
  relation: RelationFieldInfo | null;
}): DataToolbarGroupOption | undefined {
  if (!relation || group === false) return undefined;
  const resolvedGroup = group;
  if (!resolvedGroup && !aggregateKey) return undefined;
  const defaultGroup = {
    field: `${field}.${labelField ?? relation.labelField}`,
    aggregateField: field,
    aggregateKey: aggregateKey ?? field,
  };
  const optionGroup = resolvedGroup ?? defaultGroup;
  return {
    id: optionGroup.field,
    label,
    group: optionGroup,
  };
}

function relationLabel(field: string): string {
  return field.charAt(0).toUpperCase() + field.slice(1);
}
