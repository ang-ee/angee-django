import * as React from "react";
import {
  useAngeeFacets,
} from "../data/hooks";
import type {
  FacetRequestSpec,
  } from "@angee/refine";
import {
  useModelMetadata,
} from "@angee/resources";
import {
  useSchemaFieldMetadata,
  type ModelMetadata,
  type ModelRelationFilterMetadata,
  type ModelRelationFilterMode,
  type SchemaFieldMetadata,
} from "@angee/resources";

import type {
  ResourceToolbarFilterField,
  ResourceToolbarFilterOption,
  ResourceToolbarGroupOption,
} from "../toolbars";
import type { ResourceViewFilter, ResourceViewGroup } from "./resource-view-model";
import { facetRequestSpec } from "./facet-query";
import {
  resourceViewGroupToAggregateDimension,
  groupLabelDimension,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
} from "./ListInternals";
import {
  groupLabel,
  relationFieldInfo,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import type { FacetDescriptor } from "./page";

const RELATION_FACET_OPTION_LIMIT = 200;
const EMPTY_FILTER_OPTIONS: readonly ResourceToolbarFilterOption[] = [];
const EMPTY_FILTER_FIELDS: readonly ResourceToolbarFilterField[] = [];
const EMPTY_GROUP_OPTIONS: readonly ResourceToolbarGroupOption[] = [];
const EMPTY_FACET_SPECS: readonly FacetRequestSpec[] = [];
const EMPTY_RELATION_FACET_OPTIONS: readonly RelationFacetOptions[] = [];
const EMPTY_DECLARED_RELATION_FACETS: RelationFacets = {
  filters: EMPTY_FILTER_OPTIONS,
  filterFields: EMPTY_FILTER_FIELDS,
  groupOptions: EMPTY_GROUP_OPTIONS,
};

export type RelationFacetOptions = FacetDescriptor;

export interface RelationFacets {
  filters: readonly ResourceToolbarFilterOption[];
  filterFields: readonly ResourceToolbarFilterField[];
  groupOptions: readonly ResourceToolbarGroupOption[];
}

interface DeclaredRelationFacet {
  id: string;
  label: React.ReactNode;
  filter: ModelRelationFilterMetadata;
  groupOption?: ResourceToolbarGroupOption;
  spec?: FacetRequestSpec;
}

/** Build declared relation facets in one GraphQL facet query for a model list. */
export function useRelationFacets(
  resource: string,
  options: readonly RelationFacetOptions[] | undefined =
    EMPTY_RELATION_FACET_OPTIONS,
  activeFilter?: ResourceViewFilter,
): RelationFacets {
  const schemaMetadata = useSchemaFieldMetadata();
  const modelMetadata = useModelMetadata(resource);
  const facetOptions = options ?? EMPTY_RELATION_FACET_OPTIONS;
  const facets = React.useMemo(
    () => relationFacetDeclarations(facetOptions, modelMetadata, schemaMetadata),
    [facetOptions, modelMetadata, schemaMetadata],
  );
  const dataResource = modelMetadata?.resource ?? null;
  const facetSpecs = React.useMemo(
    () =>
      facets.flatMap((facet) =>
        facet.spec
          ? [facetRequestSpec(facet.spec, activeFilter, [facet.filter.field])]
          : []),
    [activeFilter, facets],
  );
  const facetQuery = useAngeeFacets(dataResource, {
    facets: facetSpecs,
    enabled: dataResource !== null && facetSpecs.length > 0,
  });
  const filters = React.useMemo<readonly ResourceToolbarFilterOption[]>(
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
  const filterFields = React.useMemo<readonly ResourceToolbarFilterField[]>(
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
  const groupOptions = React.useMemo<readonly ResourceToolbarGroupOption[]>(
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
  const label = optionLabel ?? groupLabel(field, modelMetadata?.fields[field]);
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
): ResourceViewFilter {
  if (filter.mode === "id") return { [filter.field]: value };
  const lookup = filter.lookup ?? "exact";
  return {
    [filter.field]: {
      [lookup]: lookup === "inList" ? [value] : value,
    },
  };
}

function relationFacetSpecs(
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
  options: {
    id: string | undefined;
    pageSize: number;
  },
): readonly FacetRequestSpec[] {
  if (!group || !options.id) return EMPTY_FACET_SPECS;
  const identity = resourceViewGroupToAggregateDimension(group, metadata);
  const label = groupLabelDimension(group, metadata);
  const identityDimension = hasuraGroupDimension(identity);
  const labelDimension = label ? hasuraGroupDimension(label) : null;
  const dimensions = labelDimension
    ? [identityDimension, labelDimension]
    : [identityDimension];
  const orderBy = hasuraGroupOrderForDimensions(dimensions);
  return [{
    id: options.id,
    dimensions,
    ...(orderBy ? { orderBy } : {}),
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
  group: ResourceViewGroup | false | undefined;
  label: React.ReactNode;
  labelField: string | undefined;
  relation: RelationFieldInfo | null;
}): ResourceToolbarGroupOption | undefined {
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
