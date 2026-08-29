import { recordValue, stableSerialize } from "@angee/refine";
import { RESOURCE_VIEW_LOOKUP_OPERATORS, RESOURCE_VIEW_RELATION_LOOKUP_OPERATORS } from "./capabilities";
import type { CalendarViewMode, ResourceViewFacetLookupOperator, ResourceViewGroupGranularity, ResourceViewKind, ResourceViewOrderDirection, ResourceViewSortDirection } from "./capabilities";
import type { ResourceViewFavorite } from "./favorites";
export type ResourceViewFilterPrimitive = string | number | boolean | null;
export type ResourceViewFilterValue =
  | ResourceViewFilterPrimitive
  | readonly ResourceViewFilterValue[]
  | ResourceViewLookup
  | ResourceViewFilter;
export type ResourceViewLookup = {
  [operator in ResourceViewFacetLookupOperator]?: ResourceViewFilterValue;
};
export type ResourceViewFilter = {
  [field: string]: ResourceViewFilterValue;
};
export type ResourceViewResourceOrder = Record<string, ResourceViewOrderDirection>;
// The fallback text-search field when a model declares no representation. The
// model-driven list resolves the real field from `recordRepresentation`
// (see `resolveTextFilterField`); this is only the default for metadata-less rows.
export const DEFAULT_TEXT_FILTER_FIELD = "title";

export interface ResourceViewSort {
  field: string;
  dir: ResourceViewSortDirection;
}

export interface ResourceViewGroup {
  field: string;
  aggregateField?: string;
  aggregateKey?: string;
  granularity?: ResourceViewGroupGranularity;
}

export type ResourceViewDefaultGroups = Partial<
  Record<ResourceViewKind, ResourceViewGroup | null>
>;

export interface ResourceViewInitialState {
  page?: number;
  pageSize?: number;
  sort?: ResourceViewSort | null;
  filter?: ResourceViewFilter;
  group?: ResourceViewGroup | null;
  groupStack?: readonly ResourceViewGroup[];
  selectedIds?: Iterable<string>;
  view?: ResourceViewKind;
  /** Calendar window mode; defaults to month. */
  mode?: CalendarViewMode;
  /** Calendar anchor day (`yyyy-MM-dd`); defaults to today. */
  anchor?: string;
}

export type ResourceViewAction =
  | { type: "setPage"; page: number }
  | { type: "setPageSize"; pageSize: number }
  | { type: "setSort"; sort: ResourceViewSort | null }
  | { type: "setFilter"; filter: ResourceViewFilter }
  | { type: "setGroup"; group: ResourceViewGroup | null }
  | { type: "setGroupStack"; groupStack: readonly ResourceViewGroup[] }
  | { type: "setSelectedIds"; selectedIds: Iterable<string> }
  | { type: "toggleSelectedId"; id: string; selected?: boolean }
  | { type: "clearSelectedIds" }
  | { type: "setView"; view: ResourceViewKind }
  | { type: "setMode"; mode: CalendarViewMode }
  | { type: "setAnchor"; anchor: string }
  | { type: "applyFavorite"; favorite: ResourceViewFavorite };

export interface FilterFacet {
  field: string;
  value: string;
  mode: "lookup" | "id";
  lookup?: ResourceViewFacetLookupOperator;
}

export class Filter {
  readonly value: ResourceViewFilter;

  constructor(value: unknown = {}) {
    const record = recordValue(value);
    this.value = record ? ({ ...record } as ResourceViewFilter) : {};
  }

  static from(value: unknown): Filter {
    return new Filter(value);
  }

  static combine(left: unknown, right: unknown): ResourceViewFilter {
    return Filter.from(left).and(right);
  }

  static combineOptional(left: unknown, right: unknown): ResourceViewFilter | undefined {
    const filter = Filter.combine(left, right);
    return Object.keys(filter).length > 0 ? filter : undefined;
  }

  static facetFromFilter(filter: ResourceViewFilter): FilterFacet | null {
    const [entry] = Object.entries(filter);
    if (!entry) return null;
    const [field, value] = entry;
    if (typeof value === "string") return { field, value, mode: "id" };
    const lookup = isResourceViewLookup(value) ? value : null;
    if (!lookup) return null;
    for (const operator of ["sqid", "pk", "exact"] as const) {
      const lookupValue = lookup[operator];
      if (typeof lookupValue === "string") {
        return { field, value: lookupValue, mode: "lookup", lookup: operator };
      }
    }
    const [lookupValue] = Array.isArray(lookup.inList)
      ? lookup.inList.filter((item): item is string => typeof item === "string")
      : [];
    return lookupValue
      ? { field, value: lookupValue, mode: "lookup", lookup: "inList" }
      : null;
  }

  hasEntries(): boolean {
    return Object.keys(this.value).length > 0;
  }

  withoutFields(fields: Iterable<string>): ResourceViewFilter {
    const omitted = new Set(fields);
    if (omitted.size === 0) return this.value;
    return withoutFilterFields(this.value, omitted);
  }

  and(filter: unknown): ResourceViewFilter {
    const right = recordValue(filter);
    if (!right || Object.keys(right).length === 0) return this.value;
    const next: Record<string, unknown> = { ...this.value };
    let andFilter: Record<string, unknown> | undefined;
    for (const [key, value] of Object.entries(right)) {
      if (!Object.prototype.hasOwnProperty.call(next, key)) {
        next[key] = value;
      } else if (stableSerialize(next[key]) !== stableSerialize(value)) {
        andFilter = { ...andFilter, [key]: value };
      }
    }
    if (!andFilter) return next as ResourceViewFilter;
    const existingAnd = recordValue(next.AND);
    next.AND = existingAnd ? Filter.combine(existingAnd, andFilter) : andFilter;
    return next as ResourceViewFilter;
  }

  facetValues(facet: FilterFacet | string): readonly string[] {
    const field = typeof facet === "string" ? facet : facet.field;
    const mode = typeof facet === "string" ? "lookup" : facet.mode;
    if (mode === "id") {
      const value = this.value[field];
      return typeof value === "string" ? [value] : [];
    }
    const lookup = this.lookup(field);
    if (typeof facet !== "string" && facet.lookup && facet.lookup !== "exact") {
      const lookupValue = lookup?.[facet.lookup];
      return Array.isArray(lookupValue)
        ? lookupValue.filter((value): value is string => typeof value === "string")
        : typeof lookupValue === "string"
          ? [lookupValue]
          : [];
    }
    const exact = lookup?.exact;
    if (typeof exact === "string") return [exact];
    const inList = lookup?.inList;
    return Array.isArray(inList)
      ? inList.filter((value): value is string => typeof value === "string")
      : [];
  }

  toggleFacet(facet: FilterFacet): ResourceViewFilter {
    if (facet.mode === "id") {
      const current = this.facetValues(facet);
      const next = { ...this.value };
      if (current.includes(facet.value)) delete next[facet.field];
      else next[facet.field] = facet.value;
      return next;
    }
    if (facet.lookup && facet.lookup !== "exact") {
      const current = this.facetValues(facet);
      const next = { ...this.value };
      if (facet.lookup === "inList") {
        const nextValues = current.includes(facet.value)
          ? current.filter((value) => value !== facet.value)
          : [...current, facet.value];
        if (nextValues.length === 0) delete next[facet.field];
        else next[facet.field] = { inList: nextValues };
      } else if (current.includes(facet.value)) {
        delete next[facet.field];
      } else {
        next[facet.field] = { [facet.lookup]: facet.value };
      }
      return next;
    }
    const current = this.facetValues(facet);
    const nextValues = current.includes(facet.value)
      ? current.filter((value) => value !== facet.value)
      : [...current, facet.value];
    const next = { ...this.value };
    if (nextValues.length === 0) {
      delete next[facet.field];
    } else if (nextValues.length === 1) {
      next[facet.field] = { exact: nextValues[0] };
    } else {
      next[facet.field] = { inList: nextValues };
    }
    return next;
  }

  textTerm(field = DEFAULT_TEXT_FILTER_FIELD): string {
    const value = this.lookup(field)?.iContains;
    return typeof value === "string" ? value : "";
  }

  withTextTerm(value: string, field = DEFAULT_TEXT_FILTER_FIELD): ResourceViewFilter {
    const next = { ...this.value };
    const trimmed = value.trim();
    if (trimmed) next[field] = { iContains: trimmed };
    else delete next[field];
    return next;
  }

  private lookup(field: string): ResourceViewLookup | null {
    const value = this.value[field];
    return isResourceViewLookup(value) ? value : null;
  }
}

function withoutFilterFields(
  value: unknown,
  fields: ReadonlySet<string>,
): ResourceViewFilter {
  const record = recordValue(value);
  if (!record) return {};
  const next: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(record)) {
    if (fields.has(key)) continue;
    if (isFilterControlKey(key)) {
      const child = withoutFilterControlValue(item, fields);
      if (child !== undefined) next[key] = child;
      continue;
    }
    next[key] = item;
  }
  return next as ResourceViewFilter;
}

function withoutFilterControlValue(
  value: unknown,
  fields: ReadonlySet<string>,
): unknown {
  if (Array.isArray(value)) {
    const items = value
      .map((item) => withoutFilterFields(item, fields))
      .filter((item) => Object.keys(item).length > 0);
    return items.length > 0 ? items : undefined;
  }
  const record = recordValue(value);
  if (!record) return value;
  const child = withoutFilterFields(record, fields);
  return Object.keys(child).length > 0 ? child : undefined;
}

function isFilterControlKey(value: string): boolean {
  return value === "AND"
    || value === "OR"
    || value === "NOT"
    || value === "and"
    || value === "or"
    || value === "not";
}

export function resourceViewFilterFromUnknown(value: unknown): ResourceViewFilter | null {
  if (!isResourceViewFilter(value)) return null;
  return value as ResourceViewFilter;
}

function isResourceViewLookup(value: unknown): value is ResourceViewLookup {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.getPrototypeOf(value) !== Object.prototype) return false;
  const record = value as Partial<Record<ResourceViewFacetLookupOperator, unknown>>;
  const operators = [
    ...RESOURCE_VIEW_LOOKUP_OPERATORS,
    ...RESOURCE_VIEW_RELATION_LOOKUP_OPERATORS,
  ];
  return operators.some((operator) =>
    Object.prototype.hasOwnProperty.call(record, operator),
  );
}

export function isResourceViewFilter(value: unknown): value is ResourceViewFilter {
  return isResourceViewFilterObject(value);
}

function isResourceViewFilterValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.every(isResourceViewFilterValue);
  return isResourceViewFilterObject(value);
}

function isResourceViewFilterObject(value: unknown): value is ResourceViewFilter {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.getPrototypeOf(value) !== Object.prototype) return false;
  return Object.values(value).every(isResourceViewFilterValue);
}
