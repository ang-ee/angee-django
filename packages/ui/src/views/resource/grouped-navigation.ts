import type { Row } from "@angee/metadata";
import type { GroupedRecordNav } from "./resource-view-list-body";
import type { ResourceListSnapshot } from "./resource-view-surface";

/** Publish the owning leaf scope when opening a grouped record. */
export function snapshotFromNav<TRow extends Row>(
  nav: GroupedRecordNav,
): ResourceListSnapshot<TRow> {
  const pageCount =
    nav.total === undefined ? undefined : Math.max(1, Math.ceil(nav.total / nav.pageSize));
  return {
    rows: nav.rows as readonly TRow[],
    total: nav.total,
    page: nav.page,
    pageSize: nav.pageSize,
    pageCount,
    hasNext: pageCount !== undefined && nav.page < pageCount,
    hasPrev: nav.page > 1,
    fetching: nav.fetching,
    navigationScope: {
      filter: nav.filter,
      order: nav.order,
      page: nav.page,
      pageSize: nav.pageSize,
    },
  };
}
