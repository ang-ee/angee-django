import { ResourceQuery, type ModelMetadata } from "@angee/metadata";
import type { ResourceToolbarCustomFilter, ResourceToolbarCustomFilterChip, ResourceToolbarFilterField, ResourceToolbarFilterOption, ResourceToolbarGroupOption } from "../../../toolbars";
import { DEFAULT_TEXT_FILTER_FIELD, Filter, isLookupOperator, type ResourceViewFilter, type ResourceViewLookup } from "../resource-view-model";
import { fieldLabel } from "../model-metadata-defaults";
import { customFilterChipLabel, customFilterId, isFacetFilter, isLookup, mergeById, parseCustomFilterId } from "./labels";
export function activeFilterIdsFor(
  filter: ResourceViewFilter,
  options: readonly ResourceToolbarFilterOption[],
): readonly string[] {
  const value = Filter.from(filter);
  return options.flatMap((option) => {
    const facet = Filter.facetFromFilter(option.filter);
    if (!facet) return value.hasPreset(option.filter) ? [option.id] : [];
    return value.facetValues(facet).includes(facet.value) ? [option.id] : [];
  });
}

export function nextFacetFilter(
  filter: ResourceViewFilter,
  options: readonly ResourceToolbarFilterOption[],
  id: string,
): ResourceViewFilter {
  const option = options.find((candidate) => candidate.id === id);
  const facet = option ? Filter.facetFromFilter(option.filter) : null;
  if (!option) return filter;
  if (!facet) return Filter.from(filter).togglePreset(option.filter);
  return Filter.from(filter).toggleFacet(facet);
}

/** The declared text comparison field used by the search control. */
export function resolveTextFilterField(
  metadata: ModelMetadata | null | undefined,
): string | null {
  if (!metadata) return DEFAULT_TEXT_FILTER_FIELD;
  const query = ResourceQuery.from(metadata);
  const preferred = metadata.resource.recordRepresentation;
  if (preferred && query.fields[preferred]?.filter?.operators.includes("iContains")) return preferred;
  return Object.keys(query.fields).find((name) => query.fields[name]?.filter?.operators.includes("iContains")) ?? null;
}

export function textFilterValue(
  filter: ResourceViewFilter,
  field: string | null = DEFAULT_TEXT_FILTER_FIELD,
): string {
  return field ? Filter.from(filter).textTerm(field) : "";
}

export function nextTextFilter(
  filter: ResourceViewFilter,
  value: string,
  field: string | null = DEFAULT_TEXT_FILTER_FIELD,
): ResourceViewFilter {
  return field ? Filter.from(filter).withTextTerm(value, field) : filter;
}

export function customFilterChipsFor(
  filter: ResourceViewFilter,
  filterOptions: readonly ResourceToolbarFilterOption[],
  fields: readonly ResourceToolbarFilterField[],
  textField: string | null = DEFAULT_TEXT_FILTER_FIELD,
): readonly ResourceToolbarCustomFilterChip[] {
  const chips: ResourceToolbarCustomFilterChip[] = [];
  const fieldsByName = new Map(
    fields.map((field) => [field.field ?? field.id, field]),
  );
  for (const [field, value] of Object.entries(filter)) {
    if (!isLookup(value)) continue;
    for (const [operator, operatorValue] of Object.entries(value)) {
      if (!isLookupOperator(operator)) continue;
      if (isFacetFilter(field, operator, operatorValue, filterOptions)) continue;
      // The free-text search term owns its own input, so it is not a removable chip.
      if (field === textField && operator === "iContains") {
        continue;
      }
      chips.push({
        id: customFilterId(field, operator),
        label: customFilterChipLabel({
          fieldLabel: fieldLabel(field, undefined, fieldsByName.get(field)?.label),
          operator,
          value: operatorValue,
          options: fieldsByName.get(field)?.options,
        }),
      });
    }
  }
  return chips;
}

export function addCustomFilter(
  filter: ResourceViewFilter,
  customFilter: ResourceToolbarCustomFilter,
): ResourceViewFilter {
  const next = { ...filter };
  const current = isLookup(next[customFilter.field])
    ? { ...(next[customFilter.field] as ResourceViewLookup) }
    : {};
  if (customFilter.operator === "isNotNull") {
    current.isNull = false;
  } else if (customFilter.operator === "isNull") {
    current.isNull = true;
  } else {
    current[customFilter.operator] = customFilter.value ?? null;
  }
  next[customFilter.field] = current;
  return next;
}

export function removeCustomFilter(
  filter: ResourceViewFilter,
  id: string,
): ResourceViewFilter {
  const [field, operator] = parseCustomFilterId(id);
  if (!field || !operator || !isLookupOperator(operator)) return filter;
  const current = filter[field];
  if (!isLookup(current)) return filter;
  const nextLookup = { ...current };
  delete nextLookup[operator];
  const next = { ...filter };
  if (Object.keys(nextLookup).length === 0) delete next[field];
  else next[field] = nextLookup;
  return next;
}

export function mergeFilterOptions(
  explicit: readonly ResourceToolbarFilterOption[] | undefined,
  inferred: readonly ResourceToolbarFilterOption[],
): readonly ResourceToolbarFilterOption[] {
  return mergeById(explicit, inferred);
}

export function mergeGroupOptions(
  explicit: readonly ResourceToolbarGroupOption[] | undefined,
  inferred: readonly ResourceToolbarGroupOption[],
): readonly ResourceToolbarGroupOption[] {
  return mergeById(explicit, inferred);
}

export function mergeFilterFields(
  explicit: readonly ResourceToolbarFilterField[] | undefined,
  inferred: readonly ResourceToolbarFilterField[],
): readonly ResourceToolbarFilterField[] {
  const inherited = new Map(inferred.map((field) => [field.field ?? field.id, field]));
  return mergeById(explicit, inferred).flatMap((field) => {
    const base = inherited.get(field.field ?? field.id);
    if (!base) return [field];
    const operators = base.operators === undefined ? field.operators
      : field.operators === undefined ? base.operators
      : field.operators.filter((operator) => base.operators!.includes(operator));
    if (operators?.length === 0) return [];
    return [{ ...base, ...field, ...(operators ? { operators } : {}) }];
  });
}
