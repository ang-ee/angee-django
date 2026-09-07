import * as React from "react";
import { useAngeeFacets } from "@angee/refine";
import { ResourceQuery, useModelMetadata, type GroupAxis } from "@angee/metadata";
import type { ResourceToolbarFilterField, ResourceToolbarFilterOption, ResourceToolbarGroupOption } from "../../toolbars";
import type { ResourceViewFilter } from "../resource/resource-view-model";
import { resourceFieldGroupLabel } from "../resource/model-metadata-defaults";
import type { FacetDescriptor } from "../page";
import { useGroupOperation } from "../resource/resource-operations";

const RELATION_FACET_OPTION_LIMIT = 200;
const EMPTY_OPTIONS: readonly FacetDescriptor[] = [];

export type RelationFacetOptions = FacetDescriptor;
export interface RelationFacets {
  filters: readonly ResourceToolbarFilterOption[];
  filterFields: readonly ResourceToolbarFilterField[];
  groupOptions: readonly ResourceToolbarGroupOption[];
}
interface DeclaredRelationFacet {
  field: string;
  label: React.ReactNode;
  axis: GroupAxis;
  pageSize: number;
  groupOption?: ResourceToolbarGroupOption;
}

/** Relation choices and bucket predicates share the resource query's axis. */
export function useRelationFacets(
  resource: string,
  options: readonly RelationFacetOptions[] | undefined = EMPTY_OPTIONS,
  activeFilter?: ResourceViewFilter,
): RelationFacets {
  const metadata = useModelMetadata(resource);
  const query = React.useMemo(() => metadata ? ResourceQuery.from(metadata) : null, [metadata]);
  const facets = React.useMemo<readonly DeclaredRelationFacet[]>(() => {
    if (!query) return [];
    const seen = new Set<string>();
    return (options ?? EMPTY_OPTIONS).flatMap((option) => {
      const { field } = option;
      if (seen.has(field) || !query.fields[field]?.filter || !query.axes[field]?.server || !query.axes[field]?.drill) return [];
      seen.add(field);
      const axis = query.axis(field);
      const label = option.label ?? resourceFieldGroupLabel(field, metadata?.fields[field]);
      const group = option.group === false ? null : query.group(option.group ?? { field }).spec;
      return [{ field, label, axis, pageSize: option.pageSize ?? RELATION_FACET_OPTION_LIMIT,
        ...(group ? { groupOption: { id: field, label, group } } : {}),
      }];
    });
  }, [options, query, metadata]);
  const groupOperation = useGroupOperation(metadata?.resource ?? null);
  const facetSpecs = React.useMemo(() => facets.map((facet) => ({
    id: facet.field, ...query!.toFacet(facet.field, activeFilter), pageSize: facet.pageSize,
  })), [activeFilter, facets, query]);
  const result = useAngeeFacets(groupOperation.target, {
    document: groupOperation.document, facets: facetSpecs, enabled: facetSpecs.length > 0,
  });
  return React.useMemo(() => ({
    filters: facets.flatMap((facet) => (result.facets[facet.field]?.options ?? []).flatMap((option) => {
      const filter = facet.axis.drill({ key: option.key });
      return filter ? [{ id: `${facet.field}:${option.value}`, label: option.label, chipLabel: option.label, filter }] : [];
    })),
    filterFields: facets.map((facet) => ({
      id: facet.field, field: facet.field, label: facet.label, type: "selection" as const,
      options: (result.facets[facet.field]?.options ?? []).map((option) => ({ value: option.value, label: option.label })),
    })),
    groupOptions: facets.flatMap((facet) => facet.groupOption ? [facet.groupOption] : []),
  }), [facets, result.facets]);
}
