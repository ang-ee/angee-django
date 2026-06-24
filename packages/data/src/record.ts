import { useMemo } from "react";
import {
  useCustom,
  useOne,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";
import {
  useModelMetadata,
  type DataResourceMetadata,
} from "@angee/sdk";

import {
  extractRevisions,
  revisionsRequest,
  type ResourceRevision,
} from "./operations";
import { errorFromUnknown } from "./errors";
import { refineFieldsFromPaths } from "./list";
import type { Row } from "./rows";
import type { ResourceTypeName } from "./resource-types";
import { refineResourceName } from "./resources";

const DISABLED_RESOURCE = "__angee_disabled__";
const INERT_REVISION_RESOURCE: DataResourceMetadata = {
  schemaName: "default",
  modelLabel: "",
  appLabel: "",
  modelName: "",
  publicIdField: "id",
  roots: { revisions: "__typename" },
  typeNames: {},
  capabilities: [],
  filterFields: [],
  orderFields: [],
  aggregateFields: [],
  groupByFields: [],
  revisionFields: ["id"],
  relationAxes: [],
};

export interface UseResourceRecordOptions {
  fields: readonly string[];
  enabled?: boolean;
}

export interface UseResourceRecordResult {
  record: Row | null;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export interface UseResourceRevisionsOptions {
  enabled?: boolean;
}

export interface UseResourceRevisionsResult {
  revisions: readonly ResourceRevision[];
  count: number;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useResourceRecord<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: TName,
  id: string | null | undefined,
  options: UseResourceRecordOptions,
): UseResourceRecordResult {
  const { fields, enabled = true } = options;
  const metadata = useModelMetadata(modelLabel);
  const resource = metadata?.resource ?? null;
  const active =
    enabled &&
    id !== null &&
    id !== undefined &&
    id !== "" &&
    resource !== null &&
    Boolean(resource.roots.detail);
  const refineFields = useMemo(() => refineFieldsFromPaths(fields), [fields]);
  const run = useOne<RowRecord, HttpError>({
    resource: resource ? refineResourceName(resource) : DISABLED_RESOURCE,
    id: id ?? "",
    dataProviderName: resource?.schemaName,
    meta: { fields: refineFields },
    queryOptions: { enabled: active },
  });

  return {
    record: (run.result as Row | undefined) ?? null,
    fetching: run.query.isFetching,
    error: errorFromUnknown(run.query.error),
    refetch: () => {
      void run.query.refetch();
    },
  };
}

export function useResourceRevisions<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: TName,
  id: string | null | undefined,
  options: UseResourceRevisionsOptions = {},
): UseResourceRevisionsResult {
  const { enabled = true } = options;
  const metadata = useModelMetadata(modelLabel);
  const resource = metadata?.resource ?? null;
  const active =
    enabled &&
    id !== null &&
    id !== undefined &&
    id !== "" &&
    resource !== null &&
    Boolean(resource.roots.revisions);
  const request = useMemo(
    () => revisionsRequest(resource ?? INERT_REVISION_RESOURCE, id ?? ""),
    [id, resource],
  );
  const run = useCustom<BaseRecord, HttpError>({
    url: "",
    method: "post",
    dataProviderName: request.dataProviderName,
    meta: request.meta,
    queryOptions: { enabled: active },
  });
  const data = run.query.data?.data ?? run.result.data;
  const revisions = useMemo(
    () => extractRevisions(data, request.root),
    [data, request.root],
  );

  return {
    revisions,
    count: revisions.length,
    fetching: run.query.isFetching,
    error: errorFromUnknown(run.query.error),
    refetch: () => {
      void run.query.refetch();
    },
  };
}

type RowRecord = BaseRecord & Row;
