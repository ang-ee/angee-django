import * as React from "react";
import { ResourceQuery, refineResourceName, type DataResourceMetadata } from "@angee/metadata";
import { useList, type HttpError } from "@refinedev/core";
import { listQueryMeta } from "@angee/refine";
import { listBatchTarget } from "../resource-operations";
import type { ListViewNavigationScope, RowRecord } from "./types";

/** The native server-list read shared by rendered tables and record navigation; `where`
 * is the predicate its request sends. */
export function useResourceListQuery({
  resource, scope, fields, relatedModels, enabled = true, refetchInterval,
}: {
  resource: DataResourceMetadata | null | undefined;
  scope: ListViewNavigationScope | null;
  fields: readonly string[];
  relatedModels?: readonly string[];
  enabled?: boolean;
  refetchInterval?: number | false;
}) {
  const request = React.useMemo(() => {
    if (!resource || !scope) return { meta: undefined, where: undefined, error: null };
    try {
      const query = ResourceQuery.from(resource);
      const target = listBatchTarget(resource);
      if (!target) throw new Error(`Resource ${resource.modelLabel} has no list query contract.`);
      const where = query.toWhere(scope.filter);
      return { meta: listQueryMeta(target, fields, where, query.toOrderBy(scope.order)), where, error: null };
    } catch (error) {
      return { meta: undefined, where: undefined, error: error instanceof Error ? error : new Error("Invalid resource query.") };
    }
  }, [resource, scope?.filter, scope?.order, fields]);
  const result = useList<RowRecord, HttpError, RowRecord>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    pagination: { mode: "server", currentPage: scope?.page ?? 1, pageSize: scope?.pageSize ?? 1 },
    filters: [], sorters: [], meta: request.meta,
    liveParams: { relatedModels },
    queryOptions: { enabled: enabled && Boolean(resource && scope) && !request.error, placeholderData: undefined, refetchInterval },
  });
  if (!request.error) return { ...result, where: request.where };
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
  return { ...result, result: { ...result.result, data: [], total: undefined }, query: failedQuery, where: undefined };
}
