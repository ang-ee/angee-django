import type { ReactNode } from "react";
import type { UiTranslate } from "../../../i18n";
import { enUiBundle } from "../../../i18n/en";
import { createAngeeI18nInstance } from "../../../runtime/i18n";
import { modelLabelSegment } from "@angee/metadata";
import { dedupeBy } from "../../../lib/dedupe";
import type { FilterClauseOperator, FilterClauseField, ResourceToolbarFilterOption } from "../../../toolbars";
import { Filter, type ResourceViewLookup, type ResourceViewLookupOperator, type FilterFacet } from "../resource-view-model";
import { groupFieldLabel } from "../resource-view-list-body";
const englishT = createAngeeI18nInstance(enUiBundle).getFixedT("en", "ui");
export function createLabelForResource(resource: string, t: UiTranslate, vocabularyLabel?: string): string {
  const name = vocabularyLabel ?? groupFieldLabel(modelLabelSegment(resource) || "record").toLowerCase();
  return t("resourceToolbar.createResource", { resource: name });
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
  options,
  t = englishT,
}: {
  fieldLabel: ReactNode;
  operator: ResourceViewLookupOperator;
  value: unknown;
  options?: FilterClauseField["options"];
  t?: UiTranslate;
}): ReactNode {
  if (operator === "isNull") {
    return t("search.emptyClause", { field: labelText(fieldLabel) ?? t("search.field"),
      value: t(value === false ? "search.notEmpty" : "search.empty") });
  }
  return t("search.clause", { field: labelText(fieldLabel) ?? t("search.field"),
    operator: filterOperatorLabel(operator, t), value: filterValueLabel(value, options) });
}

export function filterOperatorLabel(
  operator: FilterClauseOperator,
  t: UiTranslate = englishT,
): string {
  return t(`search.operator.${operator}`);
}

function filterValueLabel(value: unknown, options: FilterClauseField["options"]): string {
  if (Array.isArray(value)) return value.map(item => filterValueLabel(item, options)).join(", ");
  const raw = String(value ?? "");
  return labelText(options?.find(option => option.value === raw)?.label) ?? raw;
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
