import { FILTER_OPERATORS, type FilterOperator } from "./query-schema.js";
import { QueryParseError } from "./query-error.js";
import type { FilterValue, QueryFilter } from "./query.js";

export interface FilterFacet { field: string; value: string; lookup?: FilterOperator }
type Lookup = Partial<Record<FilterOperator, FilterValue>>;

/** Canonical immutable filter value; ResourceQuery validates field capabilities. */
export class Filter {
  readonly value: QueryFilter;
  constructor(value: unknown = {}) { this.value = parseFilter(value, "filter"); }
  static from(value: unknown = {}): Filter { return new Filter(value); }
  static combine(left: unknown, right: unknown): QueryFilter { return Filter.from(left).and(right); }
  static combineOptional(left: unknown, right: unknown): QueryFilter | undefined {
    const value = Filter.combine(left, right);
    return Object.keys(value).length ? value : undefined;
  }
  static facetFromFilter(value: QueryFilter): FilterFacet | null {
    const filter = Filter.from(value);
    const entries = Object.entries(filter.value);
    if (entries.length !== 1) return null;
    const [field] = entries[0]!;
    const lookup = filter.lookup(field);
    if (typeof lookup?.exact === "string")
      return { field, value: lookup.exact, lookup: "exact" };
    if (
      Array.isArray(lookup?.inList) &&
      lookup.inList.length === 1 &&
      typeof lookup.inList[0] === "string"
    )
      return { field, value: lookup.inList[0], lookup: "inList" };
    return null;
  }
  hasEntries(): boolean { return Object.keys(this.value).length > 0; }
  /** Read an AND-only projection through its declared field comparisons.
   * Authored transports with narrower Boolean syntax must reject OR/NOT rather
   * than silently dropping predicates while translating to typed inputs. */
  conjunctions(): readonly {
    field: string;
    operator: FilterOperator;
    value: FilterValue;
  }[] {
    return Object.entries(this.value).flatMap(([field, operand]) => {
      if (field === "AND")
        return (operand as readonly QueryFilter[]).flatMap((branch) =>
          Filter.from(branch).conjunctions(),
        );
      if (field === "OR" || field === "NOT")
        throw new QueryParseError(
          `filter.${field}`,
          "this projection requires AND predicates",
        );
      return Object.entries(this.lookup(field) ?? {}).map(
        ([operator, value]) => ({
          field,
          operator: operator as FilterOperator,
          value,
        }),
      );
    });
  }
  and(value: unknown): QueryFilter {
    const right = Filter.from(value).value;
    if (!Object.keys(right).length) return this.value;
    if (!this.hasEntries()) return right;
    if (JSON.stringify(this.value) === JSON.stringify(right)) return this.value;
    const result = { ...this.value };
    const conflicts: Record<string, FilterValue> = {};
    for (const [field, operand] of Object.entries(right)) {
      if (!Object.hasOwn(result, field)) result[field] = operand;
      else if (JSON.stringify(result[field]) !== JSON.stringify(operand)) conflicts[field] = operand;
    }
    if (!Object.keys(conflicts).length) return result;
    return { AND: [this.value, right] };
  }
  withoutFields(fields: Iterable<string>): QueryFilter {
    const omitted = new Set(fields);
    const remove = (filter: QueryFilter): QueryFilter =>
      Object.fromEntries(
        Object.entries(filter).flatMap(([field, operand]) => {
          if (omitted.has(field)) return [];
          if (field === "OR")
            return [[field, (operand as readonly QueryFilter[]).map(remove)]];
          if (field === "AND") {
            const branches = (operand as readonly QueryFilter[])
              .map(remove)
              .filter((branch) => Object.keys(branch).length);
            return branches.length ? [[field, branches]] : [];
          }
          if (field === "NOT") {
            const child = remove(operand as QueryFilter);
            return Object.keys(child).length ? [[field, child]] : [];
          }
          return [[field, operand]];
        }),
      );
    return remove(this.value);
  }
  /** Keep declared fields through nested Boolean branches without losing their grouping. */
  onlyFields(fields: Iterable<string>): QueryFilter {
    const kept = new Set(fields);
    const names = (filter: QueryFilter): string[] =>
      Object.entries(filter).flatMap(([field, operand]) => {
        if (field === "AND" || field === "OR")
          return (operand as readonly QueryFilter[]).flatMap(names);
        if (field === "NOT") return names(operand as QueryFilter);
        return [field];
      });
    return this.withoutFields(
      names(this.value).filter((field) => !kept.has(field)),
    );
  }
  /** An authored preset replaces the selected fields as a unit. Single-value facets toggle individually. */
  togglePreset(preset: QueryFilter): QueryFilter {
    const value = Filter.from(preset);
    const fields = Object.keys(value.value);
    const remaining = this.withoutFields(fields);
    return this.hasPreset(preset)
      ? remaining
      : Filter.combine(remaining, preset);
  }
  hasPreset(preset: QueryFilter): boolean {
    return (
      JSON.stringify(this.onlyFields(Object.keys(preset))) ===
      JSON.stringify(Filter.from(preset).value)
    );
  }
  facetValues(facet: FilterFacet | string): readonly string[] {
    const lookup = this.lookup(typeof facet === "string" ? facet : facet.field);
    const operator = typeof facet === "string" ? "exact" : facet.lookup ?? "exact";
    const value = operator === "exact" ? lookup?.exact ?? lookup?.inList : lookup?.[operator];
    return typeof value === "string" ? [value] : Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  }
  toggleFacet(facet: FilterFacet): QueryFilter {
    const current = this.facetValues(facet);
    const selected = current.includes(facet.value);
    const next = { ...this.value };
    const lookup = { ...this.lookup(facet.field) };
    if (facet.lookup && facet.lookup !== "exact" && facet.lookup !== "inList") {
      if (selected) delete lookup[facet.lookup];
      else lookup[facet.lookup] = facet.value;
    } else {
      const values = selected ? current.filter((item) => item !== facet.value) : [...current, facet.value];
      delete lookup.exact;
      delete lookup.inList;
      if (values.length) {
        if (values.length === 1 && facet.lookup !== "inList") lookup.exact = values[0]!;
        else lookup.inList = values;
      }
    }
    if (Object.keys(lookup).length) next[facet.field] = lookup as QueryFilter;
    else delete next[facet.field];
    return next;
  }
  textTerm(field: string): string {
    const value = this.lookup(field)?.iContains;
    return typeof value === "string" ? value : "";
  }
  withTextTerm(value: string, field: string): QueryFilter {
    const next = { ...this.value };
    const lookup = { ...this.lookup(field) };
    const trimmed = value.trim();
    if (trimmed) lookup.iContains = trimmed;
    else delete lookup.iContains;
    if (Object.keys(lookup).length) next[field] = lookup as QueryFilter;
    else delete next[field];
    return next;
  }
  private lookup(field: string): Lookup | undefined {
    const value = this.value[field];
    return isFilterObject(value) ? value : undefined;
  }
}

/** Structural boundary only; executable field/operator checks require a ResourceQuery. */
export function isQueryFilter(value: unknown): value is QueryFilter {
  try { parseFilter(value, "filter"); return true; } catch (error) {
    if (error instanceof QueryParseError) return false;
    throw error;
  }
}

function parseFilter(input: unknown, path: string): QueryFilter {
  if (!isFilterObject(input)) throw new QueryParseError(path, "expected a filter object");
  const result: Record<string, FilterValue> = {};
  for (const [field, value] of Object.entries(input).sort(([a], [b]) => a.localeCompare(b))) {
    const current = `${path}.${field}`;
    if (field === "AND" || field === "OR") {
      const branches = Array.isArray(value) ? value : [value];
      result[field] = branches.map((branch, index) => parseFilter(branch, `${current}[${index}]`));
    } else if (field === "NOT") result[field] = parseFilter(value, current);
    else {
      const lookup = isFilterObject(value) ? value : value === null ? { isNull: true } : Array.isArray(value) ? { inList: value } : { exact: value };
      if (!Object.keys(lookup).length) throw new QueryParseError(current, "expected at least one comparison");
      const parsed: Record<string, FilterValue> = {};
      for (const [operator, operand] of Object.entries(lookup).sort(([a], [b]) => a.localeCompare(b))) {
        if (!(FILTER_OPERATORS as readonly string[]).includes(operator)) throw new QueryParseError(`${current}.${operator}`, "unknown comparison operator");
        parsed[operator] = filterValueFromUnknown(operand, `${current}.${operator}`);
      }
      result[field] = parsed;
    }
  }
  return result;
}
export function isFilterObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}
export function filterValueFromUnknown(value: unknown, path: string): FilterValue {
  if (value === null || typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return value;
  if (Array.isArray(value)) return value.map((item, index) => filterValueFromUnknown(item, `${path}[${index}]`));
  if (isFilterObject(value)) return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, filterValueFromUnknown(item, `${path}.${key}`)]));
  throw new QueryParseError(path, "expected a JSON value");
}
