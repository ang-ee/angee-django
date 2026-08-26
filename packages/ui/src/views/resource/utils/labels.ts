import type { ReactNode } from "react";
import { modelLabelSegment } from "@angee/metadata";
import { dedupeBy } from "../../../lib/dedupe";
import type { ResourceToolbarCustomFilterOperator, ResourceToolbarFilterOption } from "../../../toolbars";
import { Filter, type ResourceViewLookup, type ResourceViewLookupOperator, type FilterFacet } from "../resource-view-model";
import { groupFieldLabel } from "../resource-view-list-body";
export function createLabelForResource(resource: string): string {
  const name = modelLabelSegment(resource) || "record";
  return `New ${groupFieldLabel(name).toLowerCase()}`;
}

export function mergeById<TOption extends { id: string }>(
  explicit: readonly TOption[] | undefined,
  inferred: readonly TOption[],
): readonly TOption[] {
  return dedupeBy([...(explicit ?? []), ...inferred], (option) => option.id);
}

export function isFacetFilter(
  field: string,
  operator: ResourceViewLookupOperator,
  value: unknown,
  options: readonly ResourceToolbarFilterOption[],
): boolean {
  const facets = options
    .map((option) => Filter.facetFromFilter(option.filter))
    .filter((facet): facet is FilterFacet => facet !== null)
    .filter((facet) => facet.field === field);
  if (facets.length === 0) return false;
  if (operator === "inList") {
    return Array.isArray(value)
      && value.every((item) => facets.some((facet) => facet.value === item));
  }
  const operatorFacets = facets.filter(
    (facet) => (facet.lookup ?? "exact") === operator,
  );
  if (operatorFacets.length === 0) return false;
  return operatorFacets.some((facet) => facet.value === value);
}

export function customFilterChipLabel({
  fieldLabel,
  operator,
  value,
}: {
  fieldLabel: ReactNode;
  operator: ResourceViewLookupOperator;
  value: unknown;
}): ReactNode {
  if (operator === "isNull") {
    return `${labelText(fieldLabel) ?? "Field"} is ${
      value === false ? "not empty" : "empty"
    }`;
  }
  return `${labelText(fieldLabel) ?? "Field"} ${filterOperatorLabel(operator)} ${
    filterValueLabel(value)
  }`;
}

export function filterOperatorLabel(
  operator: ResourceViewLookupOperator | ResourceToolbarCustomFilterOperator,
): string {
  switch (operator) {
    case "exact":
      return "is";
    case "inList":
      return "is one of";
    case "isNull":
      return "is empty";
    case "isNotNull":
      return "is not empty";
    case "iContains":
      return "contains";
    case "contains":
      return "contains (case-sensitive)";
    case "iStartsWith":
      return "starts with";
    case "startsWith":
      return "starts with (case-sensitive)";
    case "iEndsWith":
      return "ends with";
    case "endsWith":
      return "ends with (case-sensitive)";
    case "gt":
      return ">";
    case "gte":
      return ">=";
    case "lt":
      return "<";
    case "lte":
      return "<=";
  }
}

function filterValueLabel(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  return String(value ?? "");
}

export function customFilterId(field: string, operator: ResourceViewLookupOperator): string {
  return `${encodeURIComponent(field)}:${operator}`;
}

export function parseCustomFilterId(
  id: string,
): readonly [string | null, string | null] {
  const [field, operator, extra] = id.split(":");
  if (!field || !operator || extra !== undefined) return [null, null];
  return [decodeURIComponent(field), operator];
}

export function isLookup(value: unknown): value is ResourceViewLookup {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function labelText(value: ReactNode): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}
