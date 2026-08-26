import { rowPublicId, type Row } from "@angee/metadata";
import type { FilterFn } from "@tanstack/react-table";

import { readPath } from "./resource-view-list-body";
import type { ResourceViewFilter } from "./resource-view-model";

const warnedLookupOperators = new Set<string>();

export interface LocalFilterState {
  filter: ResourceViewFilter | undefined;
  textSearchField?: string;
  textSearchFields?: readonly string[];
}

export const resourceViewFilterFn: FilterFn<Row> = (row, _columnId, state) => {
  return rowMatchesFilter(row.original, state as LocalFilterState | undefined);
};

export function rowMatchesFilter(
  row: Row,
  state: LocalFilterState | undefined,
): boolean {
  const filter = state?.filter;
  if (!filter || Object.keys(filter).length === 0) return true;
  return rowMatchesFilterEntries(row, Object.entries(filter), state);
}

function rowMatchesFilterEntries(
  row: Row,
  entries: readonly [string, unknown][],
  state: LocalFilterState,
): boolean {
  return entries.every(([field, lookup]) => {
    if (field === "AND") return rowMatchesBranch(row, lookup, state, "AND");
    if (field === "OR") return rowMatchesBranch(row, lookup, state, "OR");
    if (field === "NOT") return !rowMatchesBranch(row, lookup, state, "AND");
    if (
      state.textSearchField
      && field === state.textSearchField
      && textSearchMatches(row, lookup, state.textSearchFields ?? [])
    ) {
      return true;
    }
    return matchesLocalLookup(readPath(row, field), lookup);
  });
}

function rowMatchesBranch(
  row: Row,
  branch: unknown,
  state: LocalFilterState,
  operator: "AND" | "OR",
): boolean {
  const filters = Array.isArray(branch) ? branch : [branch];
  const matches = filters.map((filter) =>
    isFilterObject(filter)
      && rowMatchesFilterEntries(row, Object.entries(filter), state),
  );
  return operator === "AND" ? matches.every(Boolean) : matches.some(Boolean);
}

function textSearchMatches(
  row: Row,
  lookup: unknown,
  textFields: readonly string[],
): boolean {
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) return false;
  const text = (lookup as Record<string, unknown>).iContains;
  if (typeof text !== "string" || text.trim() === "") return false;
  const query = text.trim().toLowerCase();
  return textFields.some((field) =>
    String(readPath(row, field) ?? "")
      .toLowerCase()
      .includes(query),
  );
}

export function matchesLocalLookup(value: unknown, lookup: unknown): boolean {
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return value === lookup;
  }
  return Object.entries(lookup as Record<string, unknown>).every(
    ([operator, operand]) => matchesLocalOperator(value, operator, operand),
  );
}

function matchesLocalOperator(
  value: unknown,
  operator: string,
  operand: unknown,
): boolean {
  switch (operator) {
    case "sqid":
    case "pk":
      return relationPublicId(value) === operand;
    case "exact":
    case "_eq":
      return value === operand;
    case "ne":
    case "_neq":
      return value !== operand;
    case "inList":
    case "_in":
      if (!Array.isArray(operand)) return ignoreMalformedLookup(operator);
      return operand.includes(value);
    case "_nin":
      if (!Array.isArray(operand)) return ignoreMalformedLookup(operator);
      return !operand.includes(value);
    case "isNull":
    case "_is_null":
      if (typeof operand !== "boolean") return ignoreMalformedLookup(operator);
      return (value == null) === operand;
    case "contains":
      if (typeof operand !== "string") return ignoreMalformedLookup(operator);
      return String(value ?? "").includes(operand);
    case "iContains":
      if (typeof operand !== "string") return ignoreMalformedLookup(operator);
      return String(value ?? "").toLowerCase().includes(operand.toLowerCase());
    case "startsWith":
      if (typeof operand !== "string") return ignoreMalformedLookup(operator);
      return String(value ?? "").startsWith(operand);
    case "iStartsWith":
      if (typeof operand !== "string") return ignoreMalformedLookup(operator);
      return String(value ?? "").toLowerCase()
        .startsWith(operand.toLowerCase());
    case "endsWith":
      if (typeof operand !== "string") return ignoreMalformedLookup(operator);
      return String(value ?? "").endsWith(operand);
    case "iEndsWith":
      if (typeof operand !== "string") return ignoreMalformedLookup(operator);
      return String(value ?? "").toLowerCase()
        .endsWith(operand.toLowerCase());
    case "gt":
    case "_gt":
      return compareLocalValues(value, operand) > 0;
    case "gte":
    case "_gte":
      return compareLocalValues(value, operand) >= 0;
    case "lt":
    case "_lt":
      return compareLocalValues(value, operand) < 0;
    case "lte":
    case "_lte":
      return compareLocalValues(value, operand) <= 0;
    case "jsonContains":
    case "_contains":
      warnIgnoredLocalLookup(operator, "cannot be evaluated locally");
      return true;
    case "iExact":
      warnIgnoredLocalLookup(operator, "is unsupported");
      return true;
    default:
      if (isFilterObject(operand)) {
        const nested = isFilterObject(value) ? readPath(value, operator) : undefined;
        return matchesLocalLookup(nested, operand);
      }
      warnIgnoredLocalLookup(operator, "is unsupported");
      return true;
  }
}

function ignoreMalformedLookup(operator: string): true {
  warnIgnoredLocalLookup(operator, "has a malformed operand");
  return true;
}

function warnIgnoredLocalLookup(operator: string, reason: string): void {
  if (warnedLookupOperators.has(operator)) return;
  warnedLookupOperators.add(operator);
  console.warn(
    `Client resource filter lookup "${operator}" ${reason}; `
      + "the lookup was ignored.",
  );
}

function relationPublicId(value: unknown): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const record = value as Row;
  return rowPublicId(record) ?? record.sqid ?? record.pk ?? value;
}

function compareLocalValues(left: unknown, right: unknown): number {
  if (left == null && right == null) return 0;
  if (left == null) return -1;
  if (right == null) return 1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  if (typeof left === "boolean" && typeof right === "boolean") {
    return Number(left) - Number(right);
  }
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function isFilterObject(value: unknown): value is ResourceViewFilter {
  return Boolean(value)
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}
