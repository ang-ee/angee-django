import * as React from "react";
import type { SearchFacet } from "../resource/search/types";
import { useAngeeFacets } from "@angee/refine";
import { Filter, ResourceQuery, useModelMetadata, type GroupAxis } from "@angee/metadata";
import type { ResourceViewFilter } from "../resource/resource-view-model";
import { resourceFieldGroupLabel } from "../resource/model-metadata-defaults";
import { groupLabel } from "../resource/resource-view-list-body";
import { useUiT } from "../../i18n";
import type { FacetDescriptor } from "../page";
import { useGroupOperation } from "../resource/resource-operations";

const RELATION_FACET_OPTION_LIMIT = 200;
const EMPTY_OPTIONS: readonly FacetDescriptor[] = [];

export type RelationFacetOptions = FacetDescriptor;
export type RelationFacets = readonly SearchFacet[];
interface DeclaredRelationFacet {
  field: string;
  label: React.ReactNode;
  axis: GroupAxis;
  pageSize: number;
  group: SearchFacet["group"];
}

/** Declared facet choices and bucket predicates share the resource query's axis. */
export function useRelationFacets(
  resource: string,
  options: readonly RelationFacetOptions[] | undefined = EMPTY_OPTIONS,
  activeFilter?: ResourceViewFilter,
): RelationFacets {
  const t = useUiT();
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
        group: group ?? false,
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
  return React.useMemo(() => facets.map((facet): SearchFacet => ({
    field: facet.field, label: facet.label, source: "relation", group: facet.group,
    options: (result.facets[facet.field]?.options ?? []).flatMap((option) => {
      const filter = facet.axis.drill({ key: option.key });
      if (!filter) return [];
      const value = Filter.facetFromFilter(filter)?.value;
      return [{ id: facet.axis.bucketId({ key: option.key }),
        label: facetOptionLabel(facet, option, metadata, t), filter,
        ...(value === undefined ? {} : { value }) }];
    }),
  })), [facets, metadata, result.facets, t]);
}

function facetOptionLabel(
  facet: DeclaredRelationFacet,
  option: { label: string; key: Record<string, unknown> },
  metadata: ReturnType<typeof useModelMetadata>,
  t: ReturnType<typeof useUiT>,
): string {
  return facet.axis.bucketIdentity({ key: option.key }) === null || metadata?.fields[facet.field]?.kind === "enum"
    ? groupLabel(facet.axis.bucketLabel({ key: option.key }), { field: facet.field }, metadata, t("list.emptyValue"), t)
    : option.label;
}
