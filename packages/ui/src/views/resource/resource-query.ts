import { Filter, ResourceQuery, type LocalQueryField, type ModelMetadata, type QueryFilter } from "@angee/metadata";
import type { ColumnDescriptor } from "../page";
import type { ResourceViewGroup } from "./resource-view-model";

/** Resource metadata or explicit local declarations supply the same query owner. */
export function queryForColumns<TRow extends object>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null | undefined,
  groups: readonly ResourceViewGroup[] = [],
): ResourceQuery {
  if (metadata) return ResourceQuery.from(metadata);
  const fields: Record<string, LocalQueryField> = Object.fromEntries(
    columns.map((column) => [column.field, { kind: "scalar" }]),
  );
  for (const group of groups) {
    fields[group.field] = { ...fields[group.field], kind: "scalar",
      ...(group.granularity ? { scalar: "DateTime" } : {}),
    };
  }
  return ResourceQuery.forRows({ fields });
}

/** Expand the rows search control across its declared columns, retaining other constraints. */
export function filterForTextSearch(
  query: ResourceQuery,
  value: unknown,
  textSearchField?: string,
  textSearchFields: readonly string[] = [],
): QueryFilter {
  const filter = Filter.from(value);
  const text = textSearchField ? filter.textTerm(textSearchField) : "";
  if (!text || !textSearchField || textSearchFields.length === 0) return query.filterFrom(filter.value);
  return query.filterFrom(Filter.combine(
    filter.withTextTerm("", textSearchField),
    { OR: textSearchFields.filter((field) => query.fields[field]?.filter?.operators.includes("iContains"))
      .map((field) => ({ [field]: { iContains: text } })) },
  ));
}
