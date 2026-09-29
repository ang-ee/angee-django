import * as React from "react";
import { Filter, ResourceQuery, rowPublicId, type DataResourceMetadata, type ResourceFilter, type ResourceOrder, type ResourceTypeName, type Row } from "@angee/metadata";
import { MAX_PAGE_SIZE, useAngeeListBatch } from "@angee/refine";

import { listBatchTarget } from "../resource/resource-operations";
import { useResourceListQuery } from "../resource/surface/resource-list-query";

/** Scope either Gantt source through its own declared lane drill, never a guessed transport field. */
export function ganttLaneFilter(query: ResourceQuery, field: string, lanes: readonly string[], baseFilter?: ResourceFilter<ResourceTypeName>) {
  const axis = query.group({ field });
  const drill = axis.declaration.drill;
  if (!drill) throw new Error(`Gantt row field "${field}" requires a drill filter.`);
  const filters = lanes.map((id) => {
    const filter = axis.drill({ key: { [drill.valueKey]: id } });
    if (!filter) throw new Error(`Gantt row field "${field}" cannot filter its related rows.`);
    return filter;
  });
  return Filter.combineOptional(baseFilter, { OR: filters });
}

/** Load every page on the selected lanes, shared by bars and point markers. */
export function useGanttRecords<TRow extends Row = Row>({
  resource, fields, filter, order, enabled,
}: {
  resource: DataResourceMetadata | null;
  fields: readonly string[];
  filter?: ResourceFilter<ResourceTypeName>;
  order?: ResourceOrder<ResourceTypeName>;
  enabled: boolean;
}) {
  const query = React.useMemo(() => resource ? ResourceQuery.from(resource) : null, [resource]);
  const first = useResourceListQuery({
    resource, fields, enabled,
    scope: { filter: filter === undefined ? undefined : Filter.from(filter).value, order, page: 1, pageSize: MAX_PAGE_SIZE },
  });
  const requests = React.useMemo(() => {
    if (!enabled || !query || first.result.total === undefined || first.query.error) return [];
    return Array.from({ length: Math.max(0, Math.ceil(first.result.total / MAX_PAGE_SIZE) - 1) }, (_, index) => ({
      key: String(index + 2), page: index + 2, pageSize: MAX_PAGE_SIZE,
      where: query.toWhere(filter), orderBy: query.toOrderBy(order),
    }));
  }, [enabled, query, first.result.total, first.query.error, filter, order]);
  const rest = useAngeeListBatch(listBatchTarget(resource), requests, { fields, enabled });
  const records = React.useMemo(() => {
    const loaded = enabled ? [...first.result.data, ...[...rest.values()].flatMap((page) => page.rows)] as TRow[] : [];
    const rows = loaded.filter((row) => Boolean(rowPublicId(row)));
    return { rows, skipped: loaded.length - rows.length };
  }, [enabled, first.result.data, rest]);
  const pages = [...rest.values()];
  const refetch = React.useCallback(() => {
    if (enabled) void first.query.refetch();
    for (const page of rest.values()) page.refetch();
  }, [enabled, first.query.refetch, rest]);
  return {
    ...records,
    fetching: enabled && (first.query.isFetching || pages.some((page) => page.fetching)),
    error: enabled ? first.query.error ?? pages.find((page) => page.error)?.error : null,
    refetch,
  };
}
