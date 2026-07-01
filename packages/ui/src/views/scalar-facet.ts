import * as React from "react";
import {
  useAngeeFacets,
} from "../data/hooks";
import type {
  FacetRequestSpec,
  ResourceFacetOption,
  } from "@angee/refine";
import {
  type ModelFieldMetadata,
} from "@angee/resources";
import type {
  ModelMetadata,
} from "@angee/resources";

import type {
  ResourceToolbarFilterField,
  ResourceToolbarFilterOption,
} from "../toolbars";
import type { ResourceViewFilter, ResourceViewGroup } from "./resource-view-model";
import { facetRequestSpec } from "./facet-query";
import {
  resourceViewGroupToAggregateDimension,
  groupKey,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
} from "./ListInternals";
import { groupLabel } from "./model-metadata-defaults";
import type { ColumnDescriptor } from "./page";

const SCALAR_FACET_OPTION_LIMIT = 200;
const EMPTY_FILTER_OPTIONS: readonly ResourceToolbarFilterOption[] = [];
const EMPTY_FILTER_FIELDS: readonly ResourceToolbarFilterField[] = [];
const EMPTY_SCALAR_FACETS: ScalarFacets = {
  filters: EMPTY_FILTER_OPTIONS,
  filterFields: EMPTY_FILTER_FIELDS,
};

export interface ScalarFacets {
  filters: readonly ResourceToolbarFilterOption[];
  filterFields: readonly ResourceToolbarFilterField[];
}

export interface ScalarFacetDeclaration {
  id: string;
  field: string;
  label: React.ReactNode;
  group: ResourceViewGroup;
  spec: FacetRequestSpec;
  neutralizeFilterFields: readonly string[];
}

/** Build server-backed scalar choice facets from the model's resource metadata. */
export function useScalarFacets<TRow extends object>(
  _model: string,
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  activeFilter?: ResourceViewFilter,
): ScalarFacets {
  const facets = React.useMemo(
    () => scalarFacetDeclarations(columns, metadata),
    [columns, metadata],
  );
  const resource = metadata?.resource ?? null;
  const facetSpecs = React.useMemo(
    () =>
      facets.map((facet) =>
        facetRequestSpec(
          facet.spec,
          activeFilter,
          facet.neutralizeFilterFields,
        )),
    [activeFilter, facets],
  );
  const facetQuery = useAngeeFacets(resource, {
    facets: facetSpecs,
    enabled: resource !== null && facetSpecs.length > 0,
  });
  const filters = React.useMemo<readonly ResourceToolbarFilterOption[]>(
    () =>
      facets.flatMap((facet) => {
        const result = facetQuery.facets[facet.id];
        return (result?.options ?? []).map((option) =>
          scalarFilterOption(facet, option, metadata),
        );
      }),
    [facetQuery.facets, facets, metadata],
  );
  const filterFields = React.useMemo<readonly ResourceToolbarFilterField[]>(
    () =>
      facets.flatMap((facet) => {
        const result = facetQuery.facets[facet.id];
        if (!result || result.options.length === 0) return [];
        return [{
          id: facet.field,
          field: facet.field,
          label: facet.label,
          type: "selection",
          options: result.options.map((option) => ({
            value: option.value,
            label: scalarFacetOptionLabel(facet, option, metadata),
          })),
        }];
      }),
    [facetQuery.facets, facets, metadata],
  );

  return React.useMemo(
    () =>
      facets.length > 0
        ? { filters, filterFields }
        : EMPTY_SCALAR_FACETS,
    [facets.length, filterFields, filters],
  );
}

function scalarFilterOption(
  facet: ScalarFacetDeclaration,
  option: ResourceFacetOption,
  metadata: ModelMetadata | null,
): ResourceToolbarFilterOption {
  const label = scalarFacetOptionLabel(facet, option, metadata);
  return {
    id: `${facet.field}:${option.value}`,
    label,
    chipLabel: label,
    filter: { [facet.field]: { exact: option.value } },
  };
}

function scalarFacetOptionLabel(
  facet: ScalarFacetDeclaration,
  option: ResourceFacetOption,
  metadata: ModelMetadata | null,
): React.ReactNode {
  const value = option.key[facet.spec.valueKey ?? facet.field] ?? option.value;
  return groupKey(value, facet.group, metadata);
}

export function scalarFacetDeclarations<TRow extends object>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
): readonly ScalarFacetDeclaration[] {
  if (!metadata?.resource) return [];
  const filterable = new Set(metadata.resource.filterFields);
  const groupable = new Set(metadata.resource.groupByFields);
  const columnsByField = new Map(columns.map((column) => [column.field, column]));
  const facets: ScalarFacetDeclaration[] = [];
  const seen = new Set<string>();

  for (const alias of metadata.resource.groupAliases ?? []) {
    if (!filterable.has(alias.aggregateField)) continue;
    if (!groupable.has(alias.aggregateField)) continue;
    const field = metadata.fields[alias.aggregateField];
    if (!isCategoricalScalar(field, columnsByField.get(alias.field))) continue;
    const group = {
      field: alias.field,
      aggregateField: alias.aggregateField,
      aggregateKey: alias.aggregateKey,
    };
    addScalarFacet(facets, seen, metadata, alias.aggregateField, group, {
      labelField: alias.field,
    });
  }

  for (const fieldName of metadata.resource.filterFields) {
    if (!groupable.has(fieldName) || seen.has(fieldName)) continue;
    const field = metadata.fields[fieldName];
    if (!isCategoricalScalar(field, columnsByField.get(fieldName))) continue;
    addScalarFacet(facets, seen, metadata, fieldName, { field: fieldName });
  }

  return facets;
}

function addScalarFacet(
  facets: ScalarFacetDeclaration[],
  seen: Set<string>,
  metadata: ModelMetadata,
  fieldName: string,
  group: ResourceViewGroup,
  options: { labelField?: string } = {},
): void {
  const identity = resourceViewGroupToAggregateDimension(group, metadata);
  const dimension = hasuraGroupDimension(identity);
  const orderBy = hasuraGroupOrderForDimensions([dimension]);
  const labelField = options.labelField ?? fieldName;
  const label = groupLabel(labelField, metadata.fields[labelField]);
  seen.add(fieldName);
  facets.push({
    id: fieldName,
    field: fieldName,
    label,
    group,
    spec: {
      id: fieldName,
      dimensions: [dimension],
      ...(orderBy ? { orderBy } : {}),
      ...(dimension.key ? { valueKey: dimension.key } : {}),
      pageSize: SCALAR_FACET_OPTION_LIMIT,
    },
    neutralizeFilterFields: [fieldName],
  });
}

function isCategoricalScalar<TRow extends object>(
  field: ModelFieldMetadata | undefined,
  column: ColumnDescriptor<TRow> | undefined,
): boolean {
  if (field?.kind === "enum") return true;
  if (field?.kind !== "scalar" || field.scalar !== "String") return false;
  if (column?.options && column.options.length > 0) return true;
  if (column?.tone) return true;
  return column?.widget === "statusBadge" || field.name === "status";
}
