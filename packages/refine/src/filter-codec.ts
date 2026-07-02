import {
  type CrudFilters,
  type CrudSorting,
  type Fields,
  type LogicalFilter,
} from "@refinedev/core";
import { isRecord } from "./dialect/wire";

type FieldTree = Map<string, FieldTree>;
type UnsupportedFilter = { field: string; operator: string };
type HasuraOrderBy = Record<string, unknown>;

export const ANGEE_FILTER_LOOKUP_OPERATORS = [
  "exact",
  "inList",
  "isNull",
  "contains",
  "iContains",
  "startsWith",
  "iStartsWith",
  "endsWith",
  "iEndsWith",
  "gt",
  "gte",
  "lt",
  "lte",
] as const;
export type AngeeFilterLookupOperator =
  (typeof ANGEE_FILTER_LOOKUP_OPERATORS)[number];

// The offered text vocabulary — every entry must round-trip the FULL wire
// stack (refine provider encoding AND the backend lookup registry). The
// case-sensitive startsWith/endsWith variants ride the provider's `_similar`
// encoding, which the backend deliberately leaves unmapped, so they are not
// offered (the codec still maps them for URL-supplied filters).
export const ANGEE_TEXT_FILTER_LOOKUP_OPERATORS = [
  "contains",
  "iContains",
  "iStartsWith",
  "iEndsWith",
  "isNull",
] as const satisfies readonly AngeeFilterLookupOperator[];

export function refineFieldsFromPaths(paths: readonly string[]): Fields {
  const root: FieldTree = new Map();
  for (const path of paths) {
    addFieldPath(root, path);
  }
  return fieldTreeToFields(root);
}

export function refineSortersFromAngeeOrder(
  order: unknown,
): CrudSorting | undefined {
  if (!isRecord(order)) return undefined;
  const sorters = Object.entries(order).flatMap(([field, direction]) => {
    if (direction === undefined || direction === null) return [];
    return [{
      field,
      order: String(direction).toLowerCase() === "desc" ? "desc" : "asc",
    } as const];
  });
  return sorters.length > 0 ? sorters : undefined;
}

export function hasuraOrderByFromAngeeOrder(
  order: unknown,
): HasuraOrderBy | undefined {
  const sorters = refineSortersFromAngeeOrder(order);
  if (!sorters) return undefined;
  const orderBy: HasuraOrderBy = {};
  for (const sorter of sorters) {
    setNestedOrder(orderBy, sorter.field, sorter.order);
  }
  return Object.keys(orderBy).length > 0 ? orderBy : undefined;
}

export function crudFiltersFromFilterRecord(
  filter: unknown,
): CrudFilters | undefined {
  const filters = filtersFromRecord(filter);
  return filters.length > 0 ? filters : undefined;
}

export function hasuraWhereFromCrudFilters(
  filters: CrudFilters | undefined,
): Record<string, unknown> | undefined {
  const where = hasuraWhereFromCrudFilterList(filters ?? []);
  return Object.keys(where).length > 0 ? where : undefined;
}

function addFieldPath(tree: FieldTree, rawPath: string): void {
  const path = rawPath.trim();
  if (!path) return;
  const [head, ...tail] = path.split(".").filter(Boolean);
  if (!head) return;
  if (tail.length === 0) {
    tree.set(head, tree.get(head) ?? new Map());
    return;
  }
  const child = tree.get(head) ?? new Map();
  tree.set(head, child);
  addFieldPath(child, tail.join("."));
}

function fieldTreeToFields(tree: FieldTree): Fields {
  return [...tree.entries()].map(([field, child]) =>
    child.size === 0 ? field : { [field]: fieldTreeToFields(child) },
  );
}

function filtersFromRecord(filter: unknown): CrudFilters {
  if (!isRecord(filter)) return [];
  const filters: CrudFilters = [];
  for (const [field, lookup] of Object.entries(filter)) {
    if (isAndKey(field) || isOrKey(field)) {
      const children = filtersFromBranch(lookup);
      if (children.length > 0) {
        filters.push({
          operator: isOrKey(field) ? "or" : "and",
          value: children,
        });
      }
      continue;
    }
    if (isNotKey(field)) {
      warnUnsupportedFilter(
        "The refine/Hasura list provider does not support Angee NOT filters yet.",
      );
      continue;
    }
    filters.push(...filtersForLookup(field, lookup));
  }
  return filters;
}

function filtersFromBranch(branch: unknown): CrudFilters {
  const items = Array.isArray(branch) ? branch : [branch];
  return items.flatMap(filtersFromRecord);
}

function hasuraWhereFromCrudFilterList(filters: CrudFilters): Record<string, unknown> {
  const where: Record<string, unknown> = {};
  for (const filter of filters) {
    if (filter.operator === "or" || filter.operator === "and") {
      const children = hasuraWhereFromCrudBranches(filter.operator, filter.value);
      if (children.length > 0) {
        where[filter.operator === "or" ? "_or" : "_and"] = children;
      }
      continue;
    }
    if (!isLogicalCrudFilter(filter)) {
      warnUnsupportedFilter(
        `Unsupported refine/Hasura conditional filter "${filter.operator}".`,
      );
      continue;
    }
    const comparison = hasuraComparisonForCrudFilter(filter);
    if (comparison) setNestedComparison(where, filter.field, comparison);
  }
  return where;
}

function hasuraWhereFromCrudBranches(
  operator: "and" | "or",
  value: unknown,
): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  if (operator === "or") {
    return (value as CrudFilters)
      .map((filter) => hasuraWhereFromCrudFilterList([filter]))
      .filter((item) => Object.keys(item).length > 0);
  }
  const where = hasuraWhereFromCrudFilterList(value as CrudFilters);
  return Object.keys(where).length > 0 ? [where] : [];
}

function filtersForLookup(field: string, lookup: unknown): CrudFilters {
  if (!isRecord(lookup) || Array.isArray(lookup)) {
    return [{ field, operator: "eq", value: lookup }];
  }

  const filters: CrudFilters = [];
  for (const [operator, value] of Object.entries(lookup)) {
    if (isUnsupportedRefineLookupOperator(operator)) {
      warnUnsupportedFilter(unsupportedFilter({ field, operator }).message);
      continue;
    }
    const refineOperator = lookupOperator(operator);
    if (refineOperator) {
      filters.push({
        field,
        operator: refineOperator.operator,
        value: refineOperator.value === KEEP_VALUE ? value : refineOperator.value,
      });
      continue;
    }
    if (isRecord(value)) {
      filters.push(...filtersForLookup(`${field}.${operator}`, value));
      continue;
    }
    warnUnsupportedFilter(unsupportedFilter({ field, operator }).message);
  }
  return filters;
}

const KEEP_VALUE = Symbol("keep-value");
type HasuraOperatorValue =
  | typeof KEEP_VALUE
  | ((value: unknown) => unknown);

function lookupOperator(
  operator: string,
): { operator: LogicalFilter["operator"]; value: unknown | typeof KEEP_VALUE } | null {
  switch (operator) {
    case "exact":
    case "sqid":
    case "pk":
    case "_eq":
      return { operator: "eq", value: KEEP_VALUE };
    case "ne":
    case "_neq":
      return { operator: "ne", value: KEEP_VALUE };
    case "gt":
    case "_gt":
      return { operator: "gt", value: KEEP_VALUE };
    case "gte":
    case "_gte":
      return { operator: "gte", value: KEEP_VALUE };
    case "lt":
    case "_lt":
      return { operator: "lt", value: KEEP_VALUE };
    case "lte":
    case "_lte":
      return { operator: "lte", value: KEEP_VALUE };
    case "inList":
    case "_in":
      return { operator: "in", value: KEEP_VALUE };
    case "_nin":
      return { operator: "nin", value: KEEP_VALUE };
    case "isNull":
    case "_is_null":
      return { operator: "null", value: KEEP_VALUE };
    case "jsonContains":
    case "_contains":
      return null;
    case "contains":
      return { operator: "containss", value: KEEP_VALUE };
    case "iContains":
      return { operator: "contains", value: KEEP_VALUE };
    case "startsWith":
      return { operator: "startswiths", value: KEEP_VALUE };
    case "iStartsWith":
      return { operator: "startswith", value: KEEP_VALUE };
    case "endsWith":
      return { operator: "endswiths", value: KEEP_VALUE };
    case "iEndsWith":
      return { operator: "endswith", value: KEEP_VALUE };
    default:
      return null;
  }
}

function isUnsupportedRefineLookupOperator(operator: string): boolean {
  return (
    operator === "jsonContains" ||
    operator === "_contains" ||
    operator === "iExact" ||
    operator === "_ilike" ||
    operator === "_like"
  );
}

function unsupportedFilter({ field, operator }: UnsupportedFilter): Error {
  return new Error(
    `Unsupported refine/Hasura list filter "${operator}" on field "${field}".`,
  );
}

function isAndKey(value: string): boolean {
  return value === "AND" || value === "and" || value === "_and";
}

function isOrKey(value: string): boolean {
  return value === "OR" || value === "or" || value === "_or";
}

function isNotKey(value: string): boolean {
  return value === "NOT" || value === "not" || value === "_not";
}

function setNestedOrder(
  target: HasuraOrderBy,
  rawPath: string,
  value: "asc" | "desc",
): void {
  const [head, ...tail] = rawPath.split(".").filter(Boolean);
  if (!head) return;
  if (tail.length === 0) {
    target[head] = value;
    return;
  }
  const child = isRecord(target[head]) ? target[head] : {};
  target[head] = child;
  setNestedOrder(child, tail.join("."), value);
}

function hasuraComparisonForCrudFilter(
  filter: LogicalFilter,
): Record<string, unknown> | null {
  const operator = hasuraOperatorForCrudOperator(filter.operator);
  if (!operator) {
    warnUnsupportedFilter(unsupportedFilter({
      field: filter.field,
      operator: filter.operator,
    }).message);
    return null;
  }
  return {
    [operator.operator]: operator.value === KEEP_VALUE
      ? filter.value
      : operator.value(filter.value),
  };
}

function hasuraOperatorForCrudOperator(
  operator: LogicalFilter["operator"],
): { operator: string; value: HasuraOperatorValue } | null {
  switch (operator) {
    case "eq":
      return { operator: "_eq", value: KEEP_VALUE };
    case "ne":
      return { operator: "_neq", value: KEEP_VALUE };
    case "lt":
      return { operator: "_lt", value: KEEP_VALUE };
    case "lte":
      return { operator: "_lte", value: KEEP_VALUE };
    case "gt":
      return { operator: "_gt", value: KEEP_VALUE };
    case "gte":
      return { operator: "_gte", value: KEEP_VALUE };
    case "in":
      return { operator: "_in", value: KEEP_VALUE };
    case "nin":
      return { operator: "_nin", value: KEEP_VALUE };
    case "null":
      return { operator: "_is_null", value: KEEP_VALUE };
    case "containss":
      return { operator: "_like", value: containsPattern };
    case "contains":
      return { operator: "_ilike", value: containsPattern };
    case "startswiths":
      return { operator: "_like", value: startsWithPattern };
    case "startswith":
      return { operator: "_ilike", value: startsWithPattern };
    case "endswiths":
      return { operator: "_like", value: endsWithPattern };
    case "endswith":
      return { operator: "_ilike", value: endsWithPattern };
    default:
      return null;
  }
}

function containsPattern(value: unknown): string {
  return `%${escapeLikePatternValue(value)}%`;
}

function startsWithPattern(value: unknown): string {
  return `${escapeLikePatternValue(value)}%`;
}

function endsWithPattern(value: unknown): string {
  return `%${escapeLikePatternValue(value)}`;
}

function escapeLikePatternValue(value: unknown): string {
  return String(value).replace(/[\\%_]/g, "\\$&");
}

function warnUnsupportedFilter(message: string): void {
  console.warn(message);
}

function setNestedComparison(
  target: Record<string, unknown>,
  rawPath: string,
  comparison: Record<string, unknown>,
): void {
  const [head, ...tail] = rawPath.split(".").filter(Boolean);
  if (!head) return;
  if (tail.length === 0) {
    target[head] = mergeRecords(target[head], comparison);
    return;
  }
  const child = isRecord(target[head]) ? target[head] : {};
  target[head] = child;
  setNestedComparison(child, tail.join("."), comparison);
}

function mergeRecords(
  left: unknown,
  right: Record<string, unknown>,
): Record<string, unknown> {
  return isRecord(left) ? { ...left, ...right } : right;
}

function isLogicalCrudFilter(filter: CrudFilters[number]): filter is LogicalFilter {
  return "field" in filter;
}
