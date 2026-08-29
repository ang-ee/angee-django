import { isClientRowModel, type ModelMetadata } from "@angee/metadata";
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
    if (!facet) return [];
    return value.facetValues(facet).includes(facet.value)
      ? [option.id]
      : [];
  });
}

export function nextFacetFilter(
  filter: ResourceViewFilter,
  options: readonly ResourceToolbarFilterOption[],
  id: string,
): ResourceViewFilter {
  const option = options.find((candidate) => candidate.id === id);
  const facet = option ? Filter.facetFromFilter(option.filter) : null;
  if (!facet) return filter;
  return Filter.from(filter).toggleFacet(facet);
}

/**
 * The field the free-text search box reads/writes — the model's title field
 * (``recordRepresentation``, e.g. ``display_name``), falling back to the generic
 * ``title`` when unknown.
 *
 * A **server** resource sends the search term as a Hasura ``where`` on this
 * field, so it must be one the resource declares filterable; otherwise the query
 * is rejected (the resource simply never declared its title field filterable). A
 * **client** row model filters in-memory, so any field is fine. When the title
 * field is not server-filterable, fall back to the first filterable text field
 * so free-text search degrades to a working field instead of 500-ing.
 */
export function resolveTextFilterField(
  metadata: ModelMetadata | null | undefined,
): string {
  const rep = metadata?.recordRepresentation ?? DEFAULT_TEXT_FILTER_FIELD;
  const resource = metadata?.resource;
  if (!resource || isClientRowModel(resource) || resource.filterFields.includes(rep)) {
    return rep;
  }
  const fields = metadata?.fields ?? {};
  const fallback = resource.filterFields.find(
    (name) => fields[name]?.kind === "scalar" && fields[name]?.scalar === "String",
  );
  return fallback ?? rep;
}

export function textFilterValue(
  filter: ResourceViewFilter,
  field: string = DEFAULT_TEXT_FILTER_FIELD,
): string {
  return Filter.from(filter).textTerm(field);
}

export function nextTextFilter(
  filter: ResourceViewFilter,
  value: string,
  field: string = DEFAULT_TEXT_FILTER_FIELD,
): ResourceViewFilter {
  return Filter.from(filter).withTextTerm(value, field);
}

export function customFilterChipsFor(
  filter: ResourceViewFilter,
  filterOptions: readonly ResourceToolbarFilterOption[],
  fields: readonly ResourceToolbarFilterField[],
  textField: string = DEFAULT_TEXT_FILTER_FIELD,
): readonly ResourceToolbarCustomFilterChip[] {
  const chips: ResourceToolbarCustomFilterChip[] = [];
  const fieldLabels = new Map(
    fields.map((field) => [field.field ?? field.id, field.label]),
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
          fieldLabel: fieldLabel(field, undefined, fieldLabels.get(field)),
          operator,
          value: operatorValue,
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
  return mergeById(explicit, inferred);
}
