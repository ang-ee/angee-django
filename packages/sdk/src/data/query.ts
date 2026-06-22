import type { AggregateMeasure } from "../aggregate-extract";
import type {
  ResourceFilter,
  ResourceOrder,
  ResourceTypeName,
} from "../resource-types";

/** A filter accepted as the model's generated input or any record. */
export type DataQueryFilter<TName extends ResourceTypeName = ResourceTypeName> =
  | ResourceFilter<TName>
  | Record<string, unknown>;

/** A single order accepted as the model's generated input or any record. */
export type DataQueryOrder<TName extends ResourceTypeName = ResourceTypeName> =
  | ResourceOrder<TName>
  | Record<string, unknown>;

/** One grouped data dimension. `key` is the returned bucket-key field. */
export interface DataQueryGroup {
  field: string;
  key?: string;
  granularity?: string;
}

/** Server-side grouped bucket ordering. */
export interface DataQueryGroupOrder {
  field: string;
  direction: "ASC" | "DESC";
}

/** Headless model data-query state shared by GraphQL and future local sources. */
export interface DataQuery<TName extends ResourceTypeName = ResourceTypeName> {
  fields?: readonly string[];
  filter?: DataQueryFilter<TName>;
  order?: DataQueryOrder<TName>;
  groups?: readonly DataQueryGroup[];
  groupOrder?: readonly DataQueryGroupOrder[];
  measures?: readonly AggregateMeasure[];
  page?: number;
  pageSize?: number;
  selectedIds?: readonly string[];
}

/** Return the GraphQL grouped-input field for one dimension. */
export function dataQueryGroupField(group: DataQueryGroup): string {
  return group.field;
}

/** Return the result bucket-key field for one dimension. */
export function dataQueryGroupKey(group: DataQueryGroup): string {
  return group.key ?? group.field;
}

/** Return a safe 1-based page number for a query. */
export function dataQueryPage(query: Pick<DataQuery, "page">): number {
  const page = Math.floor(query.page ?? 1);
  return Number.isFinite(page) ? Math.max(1, page) : 1;
}
