import {
  hasuraWhereFromAngeeFilter,
  type FacetRequestSpec,
} from "@angee/data";

import {
  Filter,
  type DataViewFilter,
} from "./data-view-model";

export function facetRequestSpec(
  spec: FacetRequestSpec,
  activeFilter: DataViewFilter | undefined,
  neutralizeFilterFields: readonly string[],
): FacetRequestSpec {
  const where = facetWhere(activeFilter, neutralizeFilterFields);
  return where ? { ...spec, where } : spec;
}

function facetWhere(
  activeFilter: DataViewFilter | undefined,
  neutralizeFilterFields: readonly string[],
): Record<string, unknown> | undefined {
  if (activeFilter === undefined) return undefined;
  return hasuraWhereFromAngeeFilter(
    Filter.from(activeFilter).withoutFields(neutralizeFilterFields),
  );
}
