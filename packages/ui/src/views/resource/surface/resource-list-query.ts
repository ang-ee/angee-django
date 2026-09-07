import * as React from "react";
import { ResourceQuery, refineResourceName, type DataResourceMetadata } from "@angee/metadata";
import { useList, type HttpError } from "@refinedev/core";
import { listQueryMeta } from "@angee/refine";
import { listBatchTarget } from "../resource-operations";
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
  const request = React.useMemo(() => {
    if (!resource || !scope) return { meta: undefined, error: null };
    try {
      const query = ResourceQuery.from(resource);
      const target = listBatchTarget(resource);
      if (!target) throw new Error(`Resource ${resource.modelLabel} has no list query contract.`);
      return { meta: listQueryMeta(target, fields, query.toWhere(scope.filter), query.toOrderBy(scope.order)), error: null };
    } catch (error) {
      return { meta: undefined, error: error instanceof Error ? error : new Error("Invalid resource query.") };
    }
  }, [resource, scope?.filter, scope?.order, fields]);
  const result = useList<RowRecord, HttpError, RowRecord>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    pagination: { mode: "server", currentPage: scope?.page ?? 1, pageSize: scope?.pageSize ?? 1 },
    filters: [], sorters: [], meta: request.meta,
    queryOptions: { enabled: enabled && Boolean(resource && scope) && !request.error, placeholderData: undefined },
  });
  if (!request.error) return result;
  const failed = {
    data: undefined,
    error: request.error,
    status: "error" as const,
    fetchStatus: "idle" as const,
    isError: true as const,
    isSuccess: false as const,
    isPending: false as const,
    isLoading: false as const,
    isInitialLoading: false as const,
    isLoadingError: true as const,
    isRefetchError: false as const,
    isFetching: false as const,
    isRefetching: false as const,
  };
  const failedQuery = { ...result.query, ...failed, refetch: async () => failedQuery };
  return { ...result, result: { ...result.result, data: [], total: undefined }, query: failedQuery };
}
