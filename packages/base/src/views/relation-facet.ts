import * as React from "react";
import {
  useAngeeFacets,
  type FacetRequestSpec,
} from "@angee/data";
import {
  useModelMetadata,
  useSchemaFieldMetadata,
  type ModelMetadata,
  type ModelRelationFilterMetadata,
  type ModelRelationFilterMode,
  type SchemaFieldMetadata,
} from "@angee/sdk";

import type {
  DataToolbarFilterField,
  DataToolbarFilterOption,
  DataToolbarGroupOption,
} from "../toolbars";
import type { DataViewFilter, DataViewGroup } from "./data-view-model";
import { facetRequestSpec } from "./facet-query";
import {
  dataViewGroupToAggregateDimension,
  groupLabelDimension,
  hasuraGroupDimension,
} from "./ListInternals";
import {
  fieldLabel,
  relationFieldInfo,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import type { FacetDescriptor } from "./page";

const RELATION_FACET_OPTION_LIMIT = 200;
const EMPTY_FILTER_OPTIONS: readonly DataToolbarFilterOption[] = [];
const EMPTY_FILTER_FIELDS: readonly DataToolbarFilterField[] = [];
const EMPTY_GROUP_OPTIONS: readonly DataToolbarGroupOption[] = [];
const EMPTY_FACET_SPECS: readonly FacetRequestSpec[] = [];
const EMPTY_RELATION_FACET_OPTIONS: readonly RelationFacetOptions[] = [];
const EMPTY_DECLARED_RELATION_FACETS: RelationFacets = {
  filters: EMPTY_FILTER_OPTIONS,
  filterFields: EMPTY_FILTER_FIELDS,
  groupOptions: EMPTY_GROUP_OPTIONS,
};

export type RelationFacetOptions = FacetDescriptor;

export interface RelationFacets {
  filters: readonly DataToolbarFilterOption[];
  filterFields: readonly DataToolbarFilterField[];
  groupOptions: readonly DataToolbarGroupOption[];
}

interface DeclaredRelationFacet {
  id: string;
  label: React.ReactNode;
  filter: ModelRelationFilterMetadata;
  groupOption?: DataToolbarGroupOption;
  spec?: FacetRequestSpec;
}

/** Build declared relation facets in one GraphQL facet query for a model list. */
export function useRelationFacets(
  model: string,
  options: readonly RelationFacetOptions[] | undefined =
    EMPTY_RELATION_FACET_OPTIONS,
  activeFilter?: DataViewFilter,
): RelationFacets {
  const schemaMetadata = useSchemaFieldMetadata();
  const modelMetadata = useModelMetadata(model);
  const facetOptions = options ?? EMPTY_RELATION_FACET_OPTIONS;
  const facets = React.useMemo(
    () => relationFacetDeclarations(facetOptions, modelMetadata, schemaMetadata),
    [facetOptions, modelMetadata, schemaMetadata],
  );
  const resource = modelMetadata?.resource ?? null;
  const facetSpecs = React.useMemo(
    () =>
      facets.flatMap((facet) =>
        facet.spec
          ? [facetRequestSpec(facet.spec, activeFilter, [facet.filter.field])]
          : []),
    [activeFilter, facets],
  );
  const facetQuery = useAngeeFacets(resource, {
    facets: facetSpecs,
    enabled: resource !== null && facetSpecs.length > 0,
  });
  const filters = React.useMemo<readonly DataToolbarFilterOption[]>(
    () =>
      facets.flatMap((facet) => {
        const result = facetQuery.facets[facet.id];
        return (result?.options ?? []).map((option) => ({
          id: `${facet.filter.field}:${option.value}`,
          label: option.label,
          chipLabel: option.label,
          filter: relationFacetFilter(facet.filter, option.value),
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
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () =>
      facets.flatMap((facet) => facet.groupOption ? [facet.groupOption] : []),
    [facets],
  );

  return React.useMemo(
    () =>
      facets.length > 0
        ? { filters, filterFields, groupOptions }
        : EMPTY_DECLARED_RELATION_FACETS,
    [facets.length, filterFields, filters, groupOptions],
  );
}

function isToolbarLookup(lookup: string | undefined): boolean {
  return lookup === undefined || lookup === "exact" || lookup === "inList";
}

function relationFacetDeclarations(
  options: readonly RelationFacetOptions[],
  modelMetadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): readonly DeclaredRelationFacet[] {
  const facets: DeclaredRelationFacet[] = [];
  const seen = new Set<string>();
  for (const option of options) {
    const facet = relationFacetDeclaration(option, modelMetadata, schemaMetadata);
    if (!facet || seen.has(facet.id)) continue;
    seen.add(facet.id);
    facets.push(facet);
  }
  return facets;
}

function relationFacetDeclaration(
  options: RelationFacetOptions,
  modelMetadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): DeclaredRelationFacet | null {
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
  const relation = relationFieldInfo(field, modelMetadata, schemaMetadata);
  if (!relation) return null;
  const filter = relationFilterConfig(relation.filter, {
    field: optionFilterField,
    mode: optionFilterMode,
  });
  if (!filter) return null;
  const aggregateKey = optionAggregateKey ?? filter.aggregateKey;
  const label = optionLabel ?? relationLabel(field);
  const groupOption = relationGroupOption({
    aggregateKey,
    field,
    group,
    labelField: optionLabelField,
    relation,
    label,
  });
  const [spec] = relationFacetSpecs(groupOption?.group, modelMetadata, {
    id: filter.field,
    pageSize,
  });
  return {
    id: filter.field,
    label,
    filter,
    ...(groupOption ? { groupOption } : {}),
    ...(spec ? { spec } : {}),
  };
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
): readonly FacetRequestSpec[] {
  if (!group || !options.id) return EMPTY_FACET_SPECS;
  const identity = dataViewGroupToAggregateDimension(group, metadata);
  const label = groupLabelDimension(group, metadata);
  const identityDimension = hasuraGroupDimension(identity);
  const labelDimension = label ? hasuraGroupDimension(label) : null;
  return [{
    id: options.id,
    dimensions: labelDimension
      ? [identityDimension, labelDimension]
      : [identityDimension],
    ...(identityDimension.key ? { valueKey: identityDimension.key } : {}),
    ...(labelDimension?.key ? { labelKey: labelDimension.key } : {}),
    pageSize: options.pageSize,
  }];
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
