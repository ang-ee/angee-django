import { ResourceQuery, isClientRowModel, type DataResourceMetadata } from "@angee/metadata";
import { MAX_PAGE_SIZE } from "@angee/refine";
import { RECORD_NAVIGATION_SEARCH_KEY as SEARCH_KEY, RECORD_TAB_SEARCH_KEY, routeSearchString } from "../../runtime/route-href";
import * as v from "valibot";
import { isResourceViewFilter } from "./model/filter";
import type { ListViewNavigationScope } from "./resource-view-surface";

export { RECORD_TAB_SEARCH_KEY } from "../../runtime/route-href";
const MAX_CONTEXT_LENGTH = 8192;
const positiveInteger = v.pipe(v.number(), v.integer(), v.minValue(1), v.maxValue(Number.MAX_SAFE_INTEGER));
const contextSchema = v.strictObject({
  version: v.literal(1),
  model: v.string(),
  schema: v.string(),
  scope: v.strictObject({
    filter: v.optional(v.custom<NonNullable<ListViewNavigationScope["filter"]>>(isResourceViewFilter)),
    order: v.optional(v.record(v.string(), v.picklist(["ASC", "DESC", "asc", "desc"]))),
    page: positiveInteger,
    pageSize: v.pipe(positiveInteger, v.maxValue(MAX_PAGE_SIZE)),
  }),
});

/** Decode only query facts bound to this active model and schema. Invalid links have no pager. */
export function parseRecordNavigationScope(
  search: Record<string, unknown>,
  resource: DataResourceMetadata | null | undefined,
): ListViewNavigationScope | null {
  const value = search[SEARCH_KEY];
  if (!resource || isClientRowModel(resource) || typeof value !== "string" || value.length > MAX_CONTEXT_LENGTH) return null;
  try {
    const parsed = v.safeParse(contextSchema, JSON.parse(value));
    if (!parsed.success || parsed.output.model !== resource.modelLabel || parsed.output.schema !== resource.schemaName) return null;
    const query = ResourceQuery.from(resource);
    query.filterFrom(parsed.output.scope.filter);
    query.sortFrom(parsed.output.scope.order);
    return { filter: parsed.output.scope.filter, order: parsed.output.scope.order, page: parsed.output.scope.page, pageSize: parsed.output.scope.pageSize };
  } catch {
    return null;
  }
}

/** Preserve the collection search verbatim, replacing only the record's navigation context. */
export function recordNavigationSearch(
  search: Record<string, unknown>,
  resource: DataResourceMetadata | null | undefined,
  scope: ListViewNavigationScope | null,
): Record<string, unknown> {
  const next = { ...search };
  delete next[SEARCH_KEY];
  if (!resource || !scope) return next;
  // A grouped snapshot also owns rows and counts. Explicit projection keeps all
  // record payloads out of portable URLs, even when that snapshot is passed here.
  const value = JSON.stringify({ version: 1, model: resource.modelLabel, schema: resource.schemaName, scope: {
    filter: scope.filter, order: scope.order, page: scope.page, pageSize: scope.pageSize,
  } });
  if (parseRecordNavigationScope({ [SEARCH_KEY]: value }, resource)) next[SEARCH_KEY] = value;
  return next;
}

/** Merge flat route-search keys while preserving every key the caller does not own. */
export function updateRouteSearch(
  patch: Readonly<Record<string, unknown>>,
): (search: Record<string, unknown>) => Record<string, unknown> {
  return (search) => ({ ...search, ...patch });
}

/** Read one string from the app's flat route-search object. */
export function routeSearchParam(
  search: Readonly<Record<string, unknown>>,
  key: string,
): string | undefined {
  const value = search[key];
  return typeof value === "string" ? value : undefined;
}

export interface RecordTargetSearch {
  tab?: string | null;
  /** Additional detail keys declared by their owning addon. Null removes a key. */
  search?: Readonly<Record<string, string | null>>;
}

/** Set or clear the portable detail context shared by canonical record links. */
export function recordTargetSearch(
  search: Readonly<Record<string, unknown>>,
  target: RecordTargetSearch,
): Record<string, unknown> {
  const next = { ...search };
  if (target.tab === null) delete next[RECORD_TAB_SEARCH_KEY];
  else if (target.tab !== undefined) next[RECORD_TAB_SEARCH_KEY] = target.tab;
  for (const [key, value] of Object.entries(target.search ?? {})) {
    if (value === null) delete next[key];
    else next[key] = value;
  }
  return next;
}

/** Add an exact tab and declared detail state to a composed canonical record href. */
export function recordTargetHref(href: string, target: RecordTargetSearch): string {
  const url = new URL(href, "https://angee.invalid");
  const query = routeSearchString(recordTargetSearch(Object.fromEntries(url.searchParams), target));
  return `${url.pathname}${query ? `?${query}` : ""}${url.hash}`;
}

/** Use the app's flat route-search codec for copied and modified-click links. */
export function recordNavigationHref(
  href: string,
  resource: DataResourceMetadata | null | undefined,
  scope: ListViewNavigationScope | null,
): string {
  const hashIndex = href.indexOf("#");
  const hash = hashIndex < 0 ? "" : href.slice(hashIndex);
  const withoutHash = hashIndex < 0 ? href : href.slice(0, hashIndex);
  const queryIndex = withoutHash.indexOf("?");
  const path = queryIndex < 0 ? withoutHash : withoutHash.slice(0, queryIndex);
  const search = queryIndex < 0 ? {} : Object.fromEntries(new URLSearchParams(withoutHash.slice(queryIndex)));
  const query = routeSearchString(recordNavigationSearch(search, resource, scope));
  return `${path}${query ? `?${query}` : ""}${hash}`;
}
