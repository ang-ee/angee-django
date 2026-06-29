import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
  type FacetRequestSpec,
} from "@angee/refine";

import {
  Filter,
  type ResourceViewFilter,
} from "./resource-view-model";

export function facetRequestSpec(
  spec: FacetRequestSpec,
  activeFilter: ResourceViewFilter | undefined,
  neutralizeFilterFields: readonly string[],
): FacetRequestSpec {
  const where = facetWhere(activeFilter, neutralizeFilterFields);
  return where ? { ...spec, where } : spec;
}

function facetWhere(
  activeFilter: ResourceViewFilter | undefined,
  neutralizeFilterFields: readonly string[],
): Record<string, unknown> | undefined {
  if (activeFilter === undefined) return undefined;
  return hasuraWhereFromCrudFilters(
    crudFiltersFromFilterRecord(
      Filter.from(activeFilter).withoutFields(neutralizeFilterFields),
    ),
  );
}
