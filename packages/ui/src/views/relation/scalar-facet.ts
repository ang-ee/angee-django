import * as React from "react";
import type { AuthoredQueryOptions } from "@angee/refine";
import type { SearchFacet } from "../resource/search/types";
import {
  useAngeeFacets,
} from "@angee/refine";
import type {
  FacetRequestSpec,
} from "@angee/refine";
import {
  Filter,
  ResourceQuery,
  type ModelFieldMetadata,
} from "@angee/metadata";
import type {
  ModelMetadata,
} from "@angee/metadata";

import type { ResourceViewFilter, ResourceViewGroup } from "../resource/resource-view-model";
import { useUiT } from "../../i18n";
import {
  groupLabel,
} from "../resource/resource-view-list-body";
import { resourceFieldGroupLabel } from "../resource/model-metadata-defaults";
import type { ColumnDescriptor } from "../page";
import { useGroupOperation } from "../resource/resource-operations";

const SCALAR_FACET_OPTION_LIMIT = 200;
export type ScalarFacets = readonly SearchFacet[];

export interface ScalarFacetDeclaration {
  id: string;
  field: string;
  label: React.ReactNode;
  group: ResourceViewGroup;
  spec: FacetRequestSpec;
}

/** Build enum choices from metadata and non-enum buckets from the server. */
export function useScalarFacets<TRow extends object>(
  _model: string,
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  activeFilter?: ResourceViewFilter,
  interests: Pick<AuthoredQueryOptions, "records" | "relatedModels"> = {},
  enabled = true,
): ScalarFacets {
  const t = useUiT();
  const facets = React.useMemo(
    () => scalarFacetDeclarations(columns, metadata),
    [columns, metadata],
  );
  const resource = metadata?.resource ?? null;
  const groupOperation = useGroupOperation(resource);
  const facetSpecs = React.useMemo(
    () => resource ? facets.filter((facet) => metadata?.fields[facet.field]?.kind !== "enum").map((facet) => ({
      ...facet.spec, ...ResourceQuery.from(resource).toFacet(facet.field, activeFilter),
    })) : [],
    [activeFilter, facets, metadata, resource],
  );
  const facetQuery = useAngeeFacets(groupOperation.target, {
    ...interests,
    document: groupOperation.document,
    facets: facetSpecs,
    enabled: enabled && resource !== null && facetSpecs.length > 0,
  });
  return React.useMemo(() => facets.map((facet): SearchFacet => {
    const query = ResourceQuery.from(metadata!);
    const axis = query.axis(facet.field);
    const field = metadata?.fields[facet.field];
    const optionForBucket = (bucket: { key: Record<string, unknown> }) => {
      const filter = axis.drill(bucket);
      if (!filter) return undefined;
      const value = Filter.facetFromFilter(filter)?.value;
      return { id: axis.bucketId(bucket), label: groupLabel(axis.bucketLabel(bucket), facet.group, metadata, t),
        filter, ...(value === undefined ? {} : { value }) };
    };
    const optionForValue = (value: string) => {
      const bucketValue = axis.declaration.drill?.valueMap?.find((entry) => Object.is(entry.to, value))?.from ?? value;
      return optionForBucket({ key: { [axis.groupBy().valueKey]: bucketValue } });
    };
    const staticOptions = field?.kind === "enum" ? [
      ...(field.values ?? []).map(({ value }) => value),
      ...(query.fields[facet.field]?.nullable
        && query.fields[facet.field]?.filter?.operators.includes("isNull") ? [null] : []),
    ].flatMap((value) => {
      const option = optionForBucket({ key: { [axis.groupBy().valueKey]: value } });
      return option ? [option] : [];
    }) : null;
    return {
      field: facet.field, label: facet.label, source: "scalar",
      optionForValue,
      options: staticOptions ?? (facetQuery.facets[facet.id]?.options ?? []).flatMap((option) => {
        const bucket = optionForBucket({ key: option.key });
        return bucket ? [bucket] : [];
      }),
    };
  }), [facets, facetQuery.facets, metadata, t]);
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
