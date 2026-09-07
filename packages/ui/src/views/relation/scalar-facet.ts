import * as React from "react";
import {
  useAngeeFacets,
} from "@angee/refine";
import type {
  FacetRequestSpec,
  ResourceFacetOption,
  } from "@angee/refine";
import {
  ResourceQuery,
  type ModelFieldMetadata,
} from "@angee/metadata";
import type {
  ModelMetadata,
} from "@angee/metadata";

import type {
  ResourceToolbarFilterField,
  ResourceToolbarFilterOption,
} from "../../toolbars";
import type { ResourceViewFilter, ResourceViewGroup } from "../resource/resource-view-model";
import { useUiT } from "../../i18n";
import type { UiTranslate } from "../../i18n";
import {
  groupLabel,
} from "../resource/resource-view-list-body";
import { resourceFieldGroupLabel } from "../resource/model-metadata-defaults";
import type { ColumnDescriptor } from "../page";
import { useGroupOperation } from "../resource/resource-operations";

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
}

/** Build server-backed scalar choice facets from the model's resource metadata. */
export function useScalarFacets<TRow extends object>(
  _model: string,
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  activeFilter?: ResourceViewFilter,
): ScalarFacets {
  const t = useUiT();
  const facets = React.useMemo(
    () => scalarFacetDeclarations(columns, metadata),
    [columns, metadata],
  );
  const resource = metadata?.resource ?? null;
  const groupOperation = useGroupOperation(resource);
  const facetSpecs = React.useMemo(
    () => resource ? facets.map((facet) => ({
      ...facet.spec, ...ResourceQuery.from(resource).toFacet(facet.field, activeFilter),
    })) : [],
    [activeFilter, facets, resource],
  );
  const facetQuery = useAngeeFacets(groupOperation.target, {
    document: groupOperation.document,
    facets: facetSpecs,
    enabled: resource !== null && facetSpecs.length > 0,
  });
  const filters = React.useMemo<readonly ResourceToolbarFilterOption[]>(
    () =>
      facets.flatMap((facet) => {
        const result = facetQuery.facets[facet.id];
        return (result?.options ?? []).map((option) =>
          scalarFilterOption(facet, option, metadata, t("list.emptyValue"), t),
        );
      }),
    [facetQuery.facets, facets, metadata, t],
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
            label: scalarFacetOptionLabel(
              facet,
              option,
              metadata,
              t("list.emptyValue"),
              t,
            ),
          })),
        }];
      }),
    [facetQuery.facets, facets, metadata, t],
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
  emptyValueLabel: string,
  t: UiTranslate,
): ResourceToolbarFilterOption {
  const label = scalarFacetOptionLabel(facet, option, metadata, emptyValueLabel, t);
  return {
    id: `${facet.field}:${option.value}`,
    label,
    chipLabel: label,
    filter: ResourceQuery.from(metadata!).axis(facet.field).drill({ key: option.key })!,
  };
}

function scalarFacetOptionLabel(
  facet: ScalarFacetDeclaration,
  option: ResourceFacetOption,
  metadata: ModelMetadata | null,
  emptyValueLabel: string,
  t: UiTranslate,
): React.ReactNode {
  const value = ResourceQuery.from(metadata!).axis(facet.field).bucketLabel({ key: option.key });
  return groupLabel(value, facet.group, metadata, emptyValueLabel, t);
}

export function scalarFacetDeclarations<TRow extends object>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
): readonly ScalarFacetDeclaration[] {
  if (!metadata?.resource) return [];
  const query = ResourceQuery.from(metadata);
  const columnsByField = new Map(columns.map((column) => [column.field, column]));
  const facets: ScalarFacetDeclaration[] = [];
  for (const [fieldName, field] of Object.entries(query.fields)) {
    if (!field.filter || !query.axes[fieldName]?.server || !query.axes[fieldName]?.drill) continue;
    if (!isCategoricalScalar(metadata.fields[fieldName], columnsByField.get(fieldName))) continue;
    const group = { field: fieldName };
    facets.push({
      id: fieldName, field: fieldName,
      label: resourceFieldGroupLabel(fieldName, metadata.fields[fieldName]), group,
      spec: { id: fieldName, ...query.toFacet(fieldName), pageSize: SCALAR_FACET_OPTION_LIMIT },
    });
  }

  return facets;
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
