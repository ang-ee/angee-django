import * as React from "react";
import { ResourceQuery, refineResourceIdentifier, refineResourceName, type DataResourceMetadata } from "@angee/metadata";
import { useKeys, useList, type HttpError } from "@refinedev/core";
import { useQueryClient } from "@tanstack/react-query";
import { listQueryMeta, sharedAuthoredMeta, useAuthoredLiveInterest, type AuthoredQueryOptions } from "@angee/refine";
import { listBatchTarget } from "../resource-operations";
import type { ListViewNavigationScope, RowRecord } from "./types";

/** The native server-list read shared by rendered tables and record navigation. */
export function useResourceListQuery({
  resource, scope, fields, enabled = true, refetchInterval, records, relatedModels,
}: {
  resource: DataResourceMetadata | null | undefined;
  scope: ListViewNavigationScope | null;
  fields: readonly string[];
  enabled?: boolean;
  refetchInterval?: number | false;
} & Pick<AuthoredQueryOptions, "records" | "relatedModels">) {
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
  const client = useQueryClient();
  const { keys } = useKeys();
  const pagination = { mode: "server" as const, currentPage: scope?.page ?? 1, pageSize: scope?.pageSize ?? 1 };
  const liveMeta = resource && request.meta ? sharedAuthoredMeta(client,
    keys().data(resource.schemaName).resource(refineResourceIdentifier(resource)).action("list")
      .params({ ...request.meta, filters: [], pagination, sorters: [] }).get(),
    [resource.canonicalLabel ?? resource.modelLabel, ...(relatedModels ?? [])], records, relatedModels,
  ) : undefined;
  useAuthoredLiveInterest(enabled && Boolean(resource && scope) && !request.error, relatedModels ?? []);
  const result = useList<RowRecord, HttpError, RowRecord>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    pagination,
    filters: [], sorters: [], meta: request.meta,
    queryOptions: { enabled: enabled && Boolean(resource && scope) && !request.error, placeholderData: undefined, refetchInterval, meta: liveMeta },
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
