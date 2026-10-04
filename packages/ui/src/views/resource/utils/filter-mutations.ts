import type { FilterClause, ResourceToolbarCustomFilterChip, FilterClauseField, ResourceToolbarFilterOption } from "../../../toolbars";
import type { ResourceQuery } from "@angee/metadata";
import { dedupeBy } from "../../../lib/dedupe";
import { DEFAULT_TEXT_FILTER_FIELD, isLookupOperator, type ResourceViewFilter, type ResourceViewLookup } from "../resource-view-model";
import { fieldLabel } from "../model-metadata-defaults";
import { customFilterChipLabel, customFilterId, isFacetFilter, isLookup, mergeById, parseCustomFilterId } from "./labels";
export function customFilterChipsFor(
  filter: ResourceViewFilter,
  filterOptions: readonly ResourceToolbarFilterOption[],
  fields: readonly FilterClauseField[],
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
  customFilter: FilterClause,
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

/** Authored ids and labels win; one executable predicate gets one choice. */
export function mergeFilterOptions(
  explicit: readonly ResourceToolbarFilterOption[] | undefined,
  inferred: readonly ResourceToolbarFilterOption[],
  query: ResourceQuery,
): readonly ResourceToolbarFilterOption[] {
  const options = mergeById(explicit, inferred);
  return dedupeBy(options, (option) => JSON.stringify(query.toWhere(option.filter)));
}

export function mergeFilterFields(
  explicit: readonly FilterClauseField[] | undefined,
  inferred: readonly FilterClauseField[],
): readonly FilterClauseField[] {
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
