import * as React from "react";
import { refineResourceName, type DataResourceMetadata } from "@angee/metadata";
import { useList, type HttpError } from "@refinedev/core";
import { crudFiltersFromFilterRecord, refineFieldsFromPaths, refineSortersFromAngeeOrder } from "@angee/refine";
import type { ListViewNavigationScope, RowRecord } from "./types";

/** The native server-list read shared by rendered tables and record navigation. */
export function useResourceListQuery({
  resource, scope, fields, enabled = true,
}: {
  resource: DataResourceMetadata | null | undefined;
  scope: ListViewNavigationScope | null;
  fields: readonly string[];
  enabled?: boolean;
}) {
  const filters = React.useMemo(() => crudFiltersFromFilterRecord(scope?.filter) ?? [], [scope?.filter]);
  const sorters = React.useMemo(() => refineSortersFromAngeeOrder(scope?.order) ?? [], [scope?.order]);
  const meta = React.useMemo(() => ({ fields: refineFieldsFromPaths(fields) }), [fields]);
  return useList<RowRecord, HttpError, RowRecord>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    pagination: { mode: "server", currentPage: scope?.page ?? 1, pageSize: scope?.pageSize ?? 1 },
    filters, sorters, meta,
    queryOptions: { enabled: enabled && Boolean(resource && scope), placeholderData: undefined },
  });
}
