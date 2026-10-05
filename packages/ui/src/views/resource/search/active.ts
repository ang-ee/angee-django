import { Filter } from "@angee/metadata";
import { stableSerialize } from "@angee/refine";
import type { FilterClauseOperator } from "../../../toolbars/FilterClauseEditor";
import type { ResourceViewState } from "../resource-view-model";
import { customFilterChipsFor } from "../utils/filter-mutations";
import { fieldLabel } from "../model-metadata-defaults";
import type { SearchActiveItem, SearchCatalog } from "./types";
import type { UiTranslate } from "../../../i18n";

/** One projection owns activity for every search presentation. */
export function activeItems(state: ResourceViewState, catalog: SearchCatalog, t?: UiTranslate): readonly SearchActiveItem[] {
  const filter = Filter.from(state.filter);
  const items: SearchActiveItem[] = [];
  const claimed = new Set<string>();
  const fields = new Set([
    ...catalog.fields.map((field) => field.field ?? field.id),
    ...catalog.facets.map((facet) => facet.field),
    ...catalog.text.map((text) => text.field),
  ]);
  for (const option of catalog.filters) {
    const facet = Filter.facetFromFilter(option.filter);
    const selected = facet ? filter.facetValues(facet).includes(facet.value) : filter.hasPreset(option.filter);
    if (!selected) continue;
    items.push({ id: `filter:${option.id}`, kind: "filter", label: option.chipLabel ?? option.label });
    const predicate = Filter.from(option.filter);
    for (const field of fields) {
      if (stableSerialize(predicate.withoutFields([field])) !== stableSerialize(predicate.value)) claimed.add(field);
    }
  }
  const facetFields = new Set<string>();
  for (const facet of catalog.facets) {
    if (claimed.has(facet.field)) continue;
    const values = filter.facetValues(facet.field);
    const options = facet.options.filter((option) => option.value === undefined
      ? filter.hasPreset(option.filter)
      : values.includes(option.value));
    for (const value of values) {
      if (options.some((option) => option.value === value)) continue;
      const option = facet.optionForValue?.(value);
      if (option) options.push(option);
    }
    if (!options.length) continue;
    facetFields.add(facet.field);
    items.push({ id: `facet:${facet.field}`, kind: "facet", field: facet.field, label: facet.label, options });
  }
  for (const text of catalog.text) {
    const value = filter.textTerm(text.field);
    if (value) items.push({ id: `text:${text.field}`, kind: "text", ...text, value });
  }
  // Keep unmatched facet comparisons editable as clauses; only an active facet
  // claims its field. Text owns iContains, not other comparisons on that field.
  let remaining = Filter.from(filter.withoutFields([...claimed, ...facetFields]));
  for (const text of catalog.text) remaining = Filter.from(remaining.withTextTerm("", text.field));
  for (const chip of customFilterChipsFor(remaining.value, [], catalog.fields, null, t)) {
    items.push({ id: `clause:${chip.id}` as `clause:${string}:${FilterClauseOperator}`, kind: "clause", label: chip.label });
  }
  state.groupStack.forEach((level, index) => {
    const axis = [...catalog.curatedGroups, ...catalog.groups].find((option) => option.group.field === level.field);
    items.push({ id: `group:${index}`, kind: "group", index, level, label: axis?.label ?? fieldLabel(level.field, undefined) });
  });
  for (const favorite of catalog.favorites) {
    if (stableSerialize(favorite.filter ?? {}) === stableSerialize(state.filter)
      && (favorite.preset ?? undefined) === (state.preset || undefined)) {
      items.push({ id: `favorite:${favorite.id}`, kind: "favorite", label: favorite.label });
    }
  }
  return items;
}
